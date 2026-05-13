"""Policy file for BORT writes.

Location: ~/.hermes/bort-policy.yaml (override via BORT_POLICY_PATH).

Each action gets one of three dispositions:
  - "auto"   : executes without prompting
  - "confirm": Hermes approval layer prompts the user once/session/always
  - "block"  : refuses, returns an error

A `per_action_max_bnb` cap (if present) is enforced regardless of disposition.
Mode "enforce" applies the policy; "warn" logs violations but allows; "off"
disables policy entirely (NOT recommended).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


DEFAULT_POLICY_PATH = "~/.hermes/bort-policy.yaml"


# Sensible Phase 1.5 defaults: autonomous for safe actions, confirm for spending.
DEFAULT_POLICY: dict[str, Any] = {
    "mode": "enforce",
    "per_action": {
        # Learning + memory + record-only: near-zero risk, autonomous
        "record_learning":    "auto",
        "record_activity":    "auto",
        "addKnowledgeSource": "auto",
        # Exits: always allow closing positions (safer than letting them stagnate)
        "close_position":     "auto",
        # Trading actions: confirm by default
        "buy_token":          "confirm",
        "sell_token":         "confirm",
        "open_position":      "confirm",
        "buy_fourmeme":       "confirm",
        "sell_fourmeme":      "confirm",
        # Campaign lifecycle (CTO): confirm
        "configure_campaign": "confirm",
        "start_campaign":     "confirm",
        "execute_buy":        "confirm",
        "execute_exit":       "confirm",
        "end_campaign":       "confirm",
        # Social posts: confirm (off-chain side-effects)
        "post_tweet":         "confirm",
        "reply_tweet":        "confirm",
        "raid_post":          "confirm",
    },
    "per_action_max_bnb": {
        "buy_token":     0.1,
        "open_position": 0.5,
        "buy_fourmeme":  0.05,
        "execute_buy":   0.5,
    },
}


@dataclass(frozen=True)
class PolicyDecision:
    """Result of checking an action against policy."""
    action: str
    disposition: str       # "auto" | "confirm" | "block"
    reason: str = ""
    max_bnb: float | None = None


class BortPolicy:
    """In-memory policy. Construct via load() or from dict."""

    def __init__(self, data: dict[str, Any]):
        self.mode: str = str(data.get("mode", "enforce")).strip().lower()
        if self.mode not in {"enforce", "warn", "off"}:
            self.mode = "enforce"
        self.per_action: dict[str, str] = {
            str(k): str(v).strip().lower()
            for k, v in (data.get("per_action") or {}).items()
        }
        self.per_action_max_bnb: dict[str, float] = {
            str(k): float(v)
            for k, v in (data.get("per_action_max_bnb") or {}).items()
        }

    @classmethod
    def load(cls, path: str | None = None) -> "BortPolicy":
        """Load from path (or BORT_POLICY_PATH env, or default location).

        If file is missing, returns the DEFAULT_POLICY: the plugin is usable
        out of the box, but users should explicitly run init-policy and tune.
        """
        resolved = Path(os.path.expanduser(
            path or os.environ.get("BORT_POLICY_PATH") or DEFAULT_POLICY_PATH
        ))
        if not resolved.exists():
            return cls(DEFAULT_POLICY)
        try:
            data = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
        except Exception:
            return cls(DEFAULT_POLICY)
        if not isinstance(data, dict):
            return cls(DEFAULT_POLICY)
        return cls(data)

    def decide(self, action: str, *, value_bnb: float = 0.0) -> PolicyDecision:
        """Return the disposition for an action.

        Off mode → always auto (don't use this in production).
        Enforce/warn → look up per_action; default to "confirm" if unknown.
        Then check per_action_max_bnb: if value exceeds cap, downgrade to "block".
        """
        if self.mode == "off":
            return PolicyDecision(action=action, disposition="auto",
                                  reason="policy mode=off")

        raw = self.per_action.get(action, "confirm")
        if raw not in {"auto", "confirm", "block"}:
            raw = "confirm"

        max_bnb = self.per_action_max_bnb.get(action)
        if max_bnb is not None and value_bnb > max_bnb + 1e-12:
            return PolicyDecision(
                action=action, disposition="block", max_bnb=max_bnb,
                reason=f"value {value_bnb} BNB exceeds per-action cap {max_bnb} BNB",
            )

        return PolicyDecision(action=action, disposition=raw, max_bnb=max_bnb,
                              reason="matched policy entry")


def write_default_policy(path: str | None = None) -> Path:
    """Write the DEFAULT_POLICY to disk. Creates parent dir if needed.

    Used by the `bort init-policy` CLI flow.
    """
    target = Path(os.path.expanduser(
        path or os.environ.get("BORT_POLICY_PATH") or DEFAULT_POLICY_PATH
    ))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(DEFAULT_POLICY, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return target
