"""bort_invoke: universal write tool. Calls any supported action on the
agent's logic contract via VPMv2.forwardHandleAction.

Replaces N specialized write tools with one. The LLM picks an action by name
(see bort_list_actions for what each logic supports), passes args matching the
action's schema, and the tool handles encoding + pre-flight + sign + broadcast.

For record_learning specifically, there's also bort_commit_learning with a
slightly friendlier schema. They both end up doing the same thing on-chain.
"""
from __future__ import annotations

import json
import os
from typing import Any

from eth_abi import encode as abi_encode
from eth_utils import keccak
from web3 import AsyncWeb3

from .. import bort_chain, bort_policy, bort_signer
from ..action_codec import (
    ACTION_SCHEMAS,
    ActionCodecError,
    encode_payload,
    get_action_schema,
    supports,
)


SEL_FORWARD_HANDLE_ACTION = "0x" + keccak(
    text="forwardHandleAction(uint256,string,address,string,bytes)"
).hex()[:8]


SCHEMA = {
    "name": "bort_invoke",
    "description": (
        "Invoke any action on a BORT agent's logic contract via VPMv2.forwardHandleAction. "
        "Pre-flights CircuitBreaker, vault permission, and policy before signing. "
        "Requires the operator to have WRITE permission granted (via bort_grant_permission_uri). "
        "Use bort_list_actions to discover the action names and parameter shapes for a given "
        "logic type (Hunter / Trading V5 / CTO). Returns a structured response with status, "
        "tx hash, and decoded result on success; structured refusal with reason on block.\n\n"
        "Common actions: buy_token, sell_token, buy_fourmeme, sell_fourmeme, check_balance, "
        "get_price, open_position (Hunter), close_position (Hunter), record_activity, "
        "record_learning. Each takes a different `args` shape: see bort_list_actions for the "
        "full param list per action."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "token_id": {
                "type": "integer",
                "description": "BAP-578 NFT token ID.",
            },
            "action": {
                "type": "string",
                "description": "Action name. Examples: 'buy_token', 'sell_token', 'open_position', 'close_position', 'record_learning'.",
            },
            "args": {
                "type": "object",
                "description": (
                    "Action-specific parameters. Shapes:\n"
                    "  buy_token       -> {token_address, amount_bnb_wei, slippage_bps?}\n"
                    "  sell_token      -> {token_address, token_amount, slippage_bps?}\n"
                    "  buy_fourmeme    -> {token_address, amount_bnb_wei, min_tokens?}\n"
                    "  sell_fourmeme   -> {token_address, token_amount}\n"
                    "  check_balance   -> {token_address}\n"
                    "  get_price       -> {token_address, amount_in, is_buy_quote}\n"
                    "  open_position   -> {token_address, amount_bnb_wei, slippage_bps?, stop_loss_bps?, take_profit_bps?}\n"
                    "  close_position  -> {token_address, slippage_bps?}\n"
                    "  record_activity -> {platform}\n"
                    "  record_learning -> {data_hash, interaction_count?}"
                ),
            },
            "value_bnb": {
                "type": "number",
                "default": 0,
                "description": "BNB to send with the call (usually 0: agents spend from their vault balance, not msg.value).",
            },
        },
        "required": ["token_id", "action", "args"],
    },
}


def _encode_forward_handle_action_calldata(
    token_id: int, vault_id: str, logic_address: str, action: str, inner_payload: bytes,
) -> str:
    args = abi_encode(
        ["uint256", "string", "address", "string", "bytes"],
        [int(token_id), vault_id, AsyncWeb3.to_checksum_address(logic_address), action, inner_payload],
    )
    return SEL_FORWARD_HANDLE_ACTION + args.hex()


async def _preflight(
    token_id: int, logic_address: str, action: str, value_bnb: float,
) -> dict[str, Any]:
    checks: dict[str, Any] = {}

    try:
        checks["global_paused"] = await bort_chain.get_global_pause()
    except Exception as e:  # noqa: BLE001
        checks["global_paused"] = {"error": f"{type(e).__name__}: {e}"}

    try:
        checks["logic_paused"] = await bort_chain.is_contract_paused(logic_address)
    except Exception as e:  # noqa: BLE001
        checks["logic_paused"] = {"error": f"{type(e).__name__}: {e}"}

    try:
        operator = bort_signer.operator_address()
        vault_id = str(token_id)
        checks["can_forward"] = await bort_chain.can_forward(token_id, vault_id, operator)
        checks["vault_id"] = vault_id
        checks["operator"] = operator
    except bort_signer.OperatorKeyMissing as e:
        checks["can_forward"] = False
        checks["operator_note"] = str(e)
    except Exception as e:  # noqa: BLE001
        checks["can_forward"] = False
        checks["operator_note"] = f"{type(e).__name__}: {e}"

    policy = bort_policy.BortPolicy.load()
    decision = policy.decide(action, value_bnb=value_bnb)
    checks["policy"] = {
        "mode":        policy.mode,
        "disposition": decision.disposition,
        "reason":      decision.reason,
        "max_bnb":     decision.max_bnb,
    }

    ok = (
        checks.get("global_paused") is False
        and checks.get("logic_paused") is False
        and checks.get("can_forward") is True
        and decision.disposition == "auto"
    )
    checks["ok"] = bool(ok)
    return checks


async def handle(args: dict[str, Any], **kwargs) -> str:
    try:
        token_id = int(args["token_id"])
        action_name = str(args["action"])
        action_args = dict(args.get("args") or {})
        value_bnb = float(args.get("value_bnb", 0))
    except (KeyError, TypeError, ValueError) as e:
        return json.dumps({"error": f"bad invocation: {e}", "tool": "bort_invoke"})

    if action_name not in ACTION_SCHEMAS:
        return json.dumps({
            "error": f"unknown action {action_name!r}. Call bort_list_actions to see supported actions.",
            "tool":  "bort_invoke",
        })

    # Resolve logic for this agent
    try:
        state = await bort_chain.get_state(token_id)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"could not resolve state: {e}", "tool": "bort_invoke"})

    logic_address = state.get("logic_address") or ""
    logic_name = state.get("logic_name")
    if not logic_address:
        return json.dumps({"error": "agent has no logic address", "tool": "bort_invoke"})
    if not supports(action_name, logic_name):
        schema = get_action_schema(action_name)
        return json.dumps({
            "error": (
                f"action {action_name!r} is not supported by logic {logic_name!r}. "
                f"Supported logics for this action: {schema['supported_logics']}."
            ),
            "tool":  "bort_invoke",
        })

    # Encode the inner payload from the friendly args
    try:
        inner_payload = encode_payload(action_name, action_args)
    except ActionCodecError as e:
        return json.dumps({"error": str(e), "tool": "bort_invoke"})

    # Effective spend for policy caps. Trading actions spend from the agent's vault
    # via `amount_bnb_wei`, not via msg.value (which is usually 0). Use the larger of
    # the two so per_action_max_bnb actually limits the spend.
    effective_bnb = value_bnb
    if "amount_bnb_wei" in action_args:
        try:
            effective_bnb = max(effective_bnb, float(action_args["amount_bnb_wei"]) / 1e18)
        except (TypeError, ValueError):
            pass

    # Pre-flight
    preflight = await _preflight(token_id, logic_address, action_name, effective_bnb)

    vault_id = str(token_id)
    calldata = _encode_forward_handle_action_calldata(
        token_id, vault_id, logic_address, action_name, inner_payload,
    )
    value_wei = int(round(value_bnb * 10 ** 18))
    vpm = bort_chain.ADDRESSES["VaultPermissionManager"]

    sim: dict[str, Any] = {"skipped": True}
    try:
        sim = await bort_signer.simulate(vpm, bytes.fromhex(calldata[2:]), value_wei=value_wei)
    except bort_signer.OperatorKeyMissing as e:
        sim = {"skipped": True, "reason": str(e)}
    except Exception as e:  # noqa: BLE001
        sim = {"skipped": True, "reason": f"{type(e).__name__}: {e}"}

    response: dict[str, Any] = {
        "token_id":      token_id,
        "action":        action_name,
        "logic_name":    logic_name,
        "logic_address": logic_address,
        "vault_id":      vault_id,
        "vpm_proxy":     vpm,
        "value_bnb":     value_bnb,
        "calldata":      calldata,
        "preflight":     preflight,
        "simulate":      sim,
    }

    # confirm-tier gate: if the ONLY thing blocking is a `confirm`-tier policy
    # (everything else passed), ask the user via Hermes' approval prompt.
    if (not preflight["ok"]
            and preflight.get("global_paused") is False
            and preflight.get("logic_paused") is False
            and preflight.get("can_forward") is True
            and preflight["policy"]["disposition"] == "confirm"):
        from ..approval import (
            request_action_approval, APPROVED, DENIED, GATEWAY_PENDING, NONINTERACTIVE,
        )
        desc = f"{action_name} for agent {token_id}" + (f" (~{effective_bnb} BNB)" if effective_bnb else "")
        verdict = request_action_approval(action_name, desc)
        response["approval"] = verdict
        if verdict == APPROVED:
            preflight["ok"] = True
            preflight["approval"] = "granted"
        elif verdict == GATEWAY_PENDING:
            response["status"] = "approval_required"
            response["reason"] = (
                f"asked the user to approve {action_name}. Re-invoke once they respond "
                f"via the gateway approval UI."
            )
            return json.dumps(response, ensure_ascii=False)
        elif verdict == DENIED:
            response["status"] = "blocked"
            response["reason"] = f"user denied {action_name}. Do not retry."
            return json.dumps(response, ensure_ascii=False)
        else:  # NONINTERACTIVE
            response["status"] = "needs_confirm"
            response["reason"] = (
                f"policy requires confirmation for {action_name!r} but there's no "
                f"interactive context. Set per_action.{action_name}: auto in "
                f"~/.hermes/bort-policy.yaml to allow it unattended."
            )
            return json.dumps(response, ensure_ascii=False)

    if not preflight["ok"]:
        if preflight.get("global_paused") is True or preflight.get("logic_paused") is True:
            response["status"] = "blocked"
            response["reason"] = "system paused via CircuitBreaker"
        elif preflight.get("can_forward") is not True:
            response["status"] = "blocked"
            response["reason"] = (
                "operator has no WRITE permission for this agent. "
                "Run bort_grant_permission_uri and have the NFT owner sign."
            )
        elif preflight["policy"]["disposition"] == "block":
            response["status"] = "blocked"
            response["reason"] = preflight["policy"].get("reason") or "policy disposition=block"
        else:
            response["status"] = "blocked"
            response["reason"] = "pre-flight failed"
        return json.dumps(response, ensure_ascii=False)

    if os.environ.get("BORT_ALLOW_BROADCAST", "").strip().lower() not in (
        "1", "true", "yes", "on",
    ):
        response["status"] = "simulated_only"
        response["reason"] = (
            "BORT_ALLOW_BROADCAST not set. Pre-flight passed, simulate succeeded, "
            "but real broadcast is disabled. Set BORT_ALLOW_BROADCAST=1 to enable."
        )
        return json.dumps(response, ensure_ascii=False)

    try:
        receipt = await bort_signer.sign_and_send(
            to=vpm, data=bytes.fromhex(calldata[2:]), value_wei=value_wei,
        )
    except Exception as e:  # noqa: BLE001
        response["status"] = "broadcast_failed"
        response["reason"] = f"{type(e).__name__}: {e}"
        return json.dumps(response, ensure_ascii=False)

    response["status"]    = "ok" if receipt.ok else "reverted"
    response["tx_hash"]   = receipt.tx_hash
    response["block"]     = receipt.block_number
    response["gas_used"]  = receipt.gas_used
    response["operator"]  = receipt.operator
    return json.dumps(response, default=str, ensure_ascii=False)


def register_invoke(ctx) -> None:
    ctx.register_tool(
        name="bort_invoke",
        toolset="bort",
        schema=SCHEMA,
        handler=handle,
        is_async=True,
        description="Universal write: invoke any agent action via VPMv2.forwardHandleAction.",
        emoji="⚡",
    )
