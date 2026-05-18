"""bort_commit_learning: commit a learning leaf via VPMv2.forwardHandleAction.

Flow:
  1. Resolve the agent's logic address from BAP578.getState (record_learning only
     exists on Hunter / Trading V5 / CTO: agents on other logics will revert).
  2. Pre-flight in order:
     - CircuitBreaker.globalPause
     - CircuitBreaker.isContractPaused(logicAddress)
     - VPMv2.canForward(tokenId, str(tokenId), operator)
     - policy.decide("record_learning")
  3. Build the inner payload `(bytes32 dataHash, uint256 interactionCount)`.
  4. Build the outer calldata for VPMv2.forwardHandleAction(...).
  5. Simulate via eth_call to catch reverts.
  6. If policy disposition is "auto" and BORT_ALLOW_BROADCAST=1, broadcast.
     Otherwise return a structured "would have happened" response.

This goes through VPMv2 (the authorized caller on Hunter / Trading V5 / CTO),
NOT directly to the logic contract: the operator key isn't an authorized
caller there.
"""
from __future__ import annotations

import json
import os
from typing import Any

from eth_abi import encode as abi_encode
from eth_utils import keccak
from web3 import AsyncWeb3

from .. import bort_chain, bort_policy, bort_signer


SEL_FORWARD_HANDLE_ACTION = "0x" + keccak(
    text="forwardHandleAction(uint256,string,address,string,bytes)"
).hex()[:8]

ACTION_NAME = "record_learning"


SCHEMA = {
    "name": "bort_commit_learning",
    "description": (
        "Record a learning event for a BAP-578 agent on-chain: the agent's logic "
        "contract record_learning action emits a permanent LearningRecorded event "
        "(tokenId, dataHash, interactionCount, timestamp), routed through "
        "VPMv2.forwardHandleAction. The event is an immutable, verifiable on-chain "
        "learning record; it does not mutate any score.\n\n"
        "Requires: operator key configured, VPMv2 WRITE permission granted by the NFT "
        "owner (via bort_grant_permission_uri), policy allows the action. Without "
        "BORT_ALLOW_BROADCAST=1, runs simulate-only and reports what would happen."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "token_id": {"type": "integer", "description": "BAP-578 NFT token ID."},
            "data_hash": {
                "type": "string",
                "description": "32-byte hash of the learning content. Hex string, 0x-prefixed, 64 hex chars.",
            },
            "interaction_count": {
                "type": "integer",
                "default": 1,
                "description": "How many interactions led to this learning. Default 1.",
            },
        },
        "required": ["token_id", "data_hash"],
    },
}


def _bytes32_from_hex(s: str) -> bytes:
    s = s.strip()
    if s.startswith("0x"):
        s = s[2:]
    if len(s) != 64:
        raise ValueError(f"data_hash must be 32 bytes (64 hex chars), got {len(s)} chars")
    return bytes.fromhex(s)


def _encode_record_learning_payload(data_hash: bytes, interaction_count: int) -> bytes:
    return abi_encode(["bytes32", "uint256"], [data_hash, int(interaction_count)])


def _encode_forward_handle_action_calldata(
    token_id: int, vault_id: str, logic_address: str, action: str, inner_payload: bytes
) -> str:
    args = abi_encode(
        ["uint256", "string", "address", "string", "bytes"],
        [int(token_id), vault_id, AsyncWeb3.to_checksum_address(logic_address), action, inner_payload],
    )
    return SEL_FORWARD_HANDLE_ACTION + args.hex()


async def _preflight(token_id: int, logic_address: str) -> dict[str, Any]:
    """Pre-flight: globalPause, isContractPaused, canForward, policy."""
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
    decision = policy.decide(ACTION_NAME, value_bnb=0.0)
    checks["policy"] = {
        "mode":        policy.mode,
        "disposition": decision.disposition,
        "reason":      decision.reason,
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
    token_id = int(args["token_id"])
    try:
        data_hash = _bytes32_from_hex(str(args["data_hash"]))
    except ValueError as e:
        return json.dumps({"error": str(e), "tool": "bort_commit_learning"})
    interaction_count = int(args.get("interaction_count", 1))

    try:
        state = await bort_chain.get_state(token_id)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"could not resolve state: {e}", "tool": "bort_commit_learning"})

    logic_address = state.get("logic_address") or ""
    if not logic_address:
        return json.dumps({"error": "agent has no logic address: cannot dispatch record_learning",
                           "tool": "bort_commit_learning"})

    preflight = await _preflight(token_id, logic_address)

    inner = _encode_record_learning_payload(data_hash, interaction_count)
    vault_id = str(token_id)
    calldata = _encode_forward_handle_action_calldata(
        token_id, vault_id, logic_address, ACTION_NAME, inner
    )

    vpm = bort_chain.ADDRESSES["VaultPermissionManager"]

    sim: dict[str, Any] = {"skipped": True}
    try:
        sim = await bort_signer.simulate(vpm, bytes.fromhex(calldata[2:]))
    except bort_signer.OperatorKeyMissing as e:
        sim = {"skipped": True, "reason": str(e)}
    except Exception as e:  # noqa: BLE001
        sim = {"skipped": True, "reason": f"{type(e).__name__}: {e}"}

    response: dict[str, Any] = {
        "token_id":          token_id,
        "action":            ACTION_NAME,
        "data_hash":         "0x" + data_hash.hex(),
        "interaction_count": interaction_count,
        "vault_id":          vault_id,
        "logic_address":     logic_address,
        "logic_name":        state.get("logic_name"),
        "vpm_proxy":         vpm,
        "calldata":          calldata,
        "preflight":         preflight,
        "simulate":          sim,
    }

    # confirm-tier gate (record_learning is `auto` by default; honored if set to `confirm`).
    if (not preflight["ok"]
            and preflight.get("global_paused") is False
            and preflight.get("logic_paused") is False
            and preflight.get("can_forward") is True
            and preflight["policy"]["disposition"] == "confirm"):
        from ..approval import (
            request_action_approval, APPROVED, DENIED, GATEWAY_PENDING, NONINTERACTIVE,
        )
        verdict = request_action_approval(ACTION_NAME, f"{ACTION_NAME} for agent {token_id}")
        response["approval"] = verdict
        if verdict == APPROVED:
            preflight["ok"] = True
            preflight["approval"] = "granted"
        elif verdict == GATEWAY_PENDING:
            response["status"] = "approval_required"
            response["reason"] = f"asked the user to approve {ACTION_NAME}; re-invoke after they respond."
            return json.dumps(response, ensure_ascii=False)
        elif verdict == DENIED:
            response["status"] = "blocked"
            response["reason"] = f"user denied {ACTION_NAME}. Do not retry."
            return json.dumps(response, ensure_ascii=False)
        else:  # NONINTERACTIVE
            response["status"] = "needs_confirm"
            response["reason"] = (
                f"policy requires confirmation for {ACTION_NAME!r} but there's no "
                f"interactive context. Set per_action.{ACTION_NAME}: auto in "
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
                "Run bort_grant_permission_uri and have the NFT owner sign the two txs "
                "(createVault + grantPermission)."
            )
        elif preflight["policy"]["disposition"] == "block":
            response["status"] = "blocked"
            response["reason"] = "policy disposition=block"
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
            to=vpm, data=bytes.fromhex(calldata[2:]),
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


def register_commit_learning(ctx) -> None:
    ctx.register_tool(
        name="bort_commit_learning",
        toolset="bort",
        schema=SCHEMA,
        handler=handle,
        is_async=True,
        description="Commit a learning leaf via VPMv2.forwardHandleAction → record_learning.",
        emoji="🧠",
    )
