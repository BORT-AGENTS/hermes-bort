"""Approval gate for confirm-tier BORT writes.

When ~/.hermes/bort-policy.yaml marks an action `confirm`, the write handlers
(bort_invoke, bort_commit_learning) call request_action_approval() instead of
refusing outright. This reuses Hermes' tools.approval machinery:

  - yolo bypass (HERMES_YOLO_MODE / session yolo)
  - per-session approval memory (approve once for the session, skip future prompts)
  - the interactive [o]nce / [s]ession / [a]lways / deny prompt in CLI sessions
  - the submit_pending flow in gateway sessions (Telegram/Discord/Slack/TUI)

Running standalone or in tests, `tools.approval` may not be importable: then we
return NONINTERACTIVE and the caller falls back to "set per_action.X: auto in
policy.yaml". Same when there's no interactive context (cron, scripts).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Verdicts returned by request_action_approval
APPROVED = "approved"
DENIED = "denied"
NONINTERACTIVE = "noninteractive"
GATEWAY_PENDING = "gateway_pending"


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def request_action_approval(action: str, description: str) -> str:
    """Ask the user to approve a confirm-tier action.

    Returns one of: APPROVED, DENIED, NONINTERACTIVE, GATEWAY_PENDING.

    APPROVED       : proceed. "once" / "session" / "always" all map here; the
                      latter two also persist so future calls skip the prompt.
    DENIED         : user said no; do not retry.
    NONINTERACTIVE : nowhere to ask (cron, script, outside Hermes). Caller should
                      fall back to telling the user to set the action to `auto`.
    GATEWAY_PENDING: prompt submitted to the gateway approval UI; the agent should
                      tell the user it's waiting and re-invoke after they answer.
    """
    try:
        from tools import approval as ha
        from tools.terminal_tool import _get_approval_callback
    except ImportError:
        return NONINTERACTIVE

    # 1. yolo bypass: opting into yolo is opting out of these prompts
    try:
        if _truthy(os.getenv("HERMES_YOLO_MODE")) or ha.is_current_session_yolo_enabled():
            return APPROVED
    except Exception:  # noqa: BLE001
        pass

    approval_key = f"bort:{action}"

    try:
        session_key = ha.get_current_session_key()
    except Exception:  # noqa: BLE001
        session_key = "default"

    # 2. already approved for this session?
    try:
        if ha.is_approved(session_key, approval_key):
            return APPROVED
    except Exception:  # noqa: BLE001
        pass

    # 3. is there anywhere to ask?
    is_cli = bool(os.getenv("HERMES_INTERACTIVE"))
    try:
        is_gateway = bool(ha._is_gateway_approval_context())
    except Exception:  # noqa: BLE001
        is_gateway = False

    if not is_cli and not is_gateway:
        return NONINTERACTIVE

    # 4. gateway: submit and let the platform's approval UI handle it
    if is_gateway:
        try:
            ha.submit_pending(session_key, {
                "command":     f"bort_invoke {action}",
                "pattern_key": approval_key,
                "description": description,
            })
            return GATEWAY_PENDING
        except Exception as e:  # noqa: BLE001
            logger.warning("bort approval: gateway submit failed: %s", e)
            return NONINTERACTIVE

    # 5. CLI interactive prompt
    try:
        cb = _get_approval_callback()
    except Exception:  # noqa: BLE001
        cb = None
    try:
        choice = ha.prompt_dangerous_approval(
            command=f"bort_invoke {action}",
            description=description,
            approval_callback=cb,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("bort approval: prompt failed: %s", e)
        return DENIED  # fail closed

    if choice == "deny":
        return DENIED
    if choice == "session":
        try:
            ha.approve_session(session_key, approval_key)
        except Exception:  # noqa: BLE001
            pass
        return APPROVED
    if choice == "always":
        try:
            ha.approve_session(session_key, approval_key)
            ha.approve_permanent(approval_key)
            ha.save_permanent_allowlist(ha._permanent_approved)  # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass
        _flip_policy_to_auto(action)
        return APPROVED
    # "once"
    return APPROVED


def _flip_policy_to_auto(action: str) -> None:
    """When the user picks 'always', also flip the action to `auto` in
    bort-policy.yaml so the policy file reflects reality. Best-effort; never raises."""
    try:
        import yaml
        from pathlib import Path
        from .bort_policy import DEFAULT_POLICY_PATH

        path = Path(os.path.expanduser(
            os.environ.get("BORT_POLICY_PATH") or DEFAULT_POLICY_PATH
        ))
        if not path.exists():
            return
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            return
        per_action = data.setdefault("per_action", {})
        if not isinstance(per_action, dict):
            return
        per_action[action] = "auto"
        path.write_text(
            yaml.safe_dump(data, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        logger.info("bort approval: flipped per_action.%s -> auto in %s", action, path)
    except Exception as e:  # noqa: BLE001
        logger.debug("bort approval: could not flip policy: %s", e)
