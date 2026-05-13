"""KnowledgeRegistryV2 delegated-write helper.

Shared by:
  - BortMemoryProvider.shutdown()  → anchor session memory as a MEMORY source
  - bort_anchor_memory tool        → same, on demand
  - bort_commit_evolution tool     → anchor an evolved skill as an INSTRUCTION source

`write_knowledge_source_delegated` does the full pre-flight (globalPause →
VPMv2.canForward → policy → approval) then simulate + (if BORT_ALLOW_BROADCAST)
broadcast `KnowledgeRegistryV2.addKnowledgeSourceDelegated(...)` via the operator
key, routed through the KR proxy. Same response shape as bort_invoke.
"""
from __future__ import annotations

import os
from typing import Any

from eth_abi import encode as abi_encode
from eth_utils import keccak
from web3 import AsyncWeb3

from . import bort_chain, bort_policy, bort_signer


# KnowledgeType enum on-chain: BASE=0, CONTEXT=1, MEMORY=2, INSTRUCTION=3, REFERENCE=4, DYNAMIC=5
KT_BASE, KT_CONTEXT, KT_MEMORY, KT_INSTRUCTION, KT_REFERENCE, KT_DYNAMIC = 0, 1, 2, 3, 4, 5
KT_NAMES = {0: "BASE", 1: "CONTEXT", 2: "MEMORY", 3: "INSTRUCTION", 4: "REFERENCE", 5: "DYNAMIC"}

SEL_ADD_DELEGATED = "0x" + keccak(
    text="addKnowledgeSourceDelegated(uint256,string,string,uint8,uint256,string,bytes32)"
).hex()[:8]

# Both memory anchoring and evolution-commit are the same on-chain op; policy keys them
# under "addKnowledgeSource" (which is `auto` in the default policy). Users can split in
# bort-policy.yaml if they want one to require confirmation.
POLICY_ACTION = "addKnowledgeSource"


def encode_add_delegated_calldata(
    token_id: int, vault_id: str, uri: str, source_type: int,
    priority: int, description: str, content_hash: bytes,
) -> str:
    args = abi_encode(
        ["uint256", "string", "string", "uint8", "uint256", "string", "bytes32"],
        [int(token_id), vault_id, uri, int(source_type), int(priority), description, content_hash],
    )
    return SEL_ADD_DELEGATED + args.hex()


async def write_knowledge_source_delegated(
    token_id: int,
    uri: str,
    source_type: int,
    *,
    priority: int = 100,
    description: str = "",
    content_hash: bytes | None = None,
) -> dict[str, Any]:
    """Anchor a knowledge source on KR v2 via the operator key.

    Returns a dict with: status (ok / reverted / blocked / needs_confirm / approval_required /
    simulated_only / broadcast_failed), tx_hash + block + gas_used on success, reason on refusal,
    plus preflight + simulate + calldata for inspection.
    """
    if content_hash is None:
        content_hash = b"\x00" * 32
    if len(content_hash) != 32:
        return {"status": "blocked", "reason": "content_hash must be 32 bytes", "tool": "bort_kr"}

    vault_id = str(int(token_id))
    kr = bort_chain.ADDRESSES["KnowledgeRegistry"]
    calldata = encode_add_delegated_calldata(token_id, vault_id, uri, source_type, priority, description, content_hash)

    # ---- pre-flight ----
    checks: dict[str, Any] = {}
    try:
        checks["global_paused"] = await bort_chain.get_global_pause()
    except Exception as e:  # noqa: BLE001
        checks["global_paused"] = {"error": f"{type(e).__name__}: {e}"}
    try:
        operator = bort_signer.operator_address()
        checks["operator"] = operator
        checks["can_forward"] = await bort_chain.can_forward(token_id, vault_id, operator)
    except bort_signer.OperatorKeyMissing as e:
        checks["can_forward"] = False
        checks["operator_note"] = str(e)
    except Exception as e:  # noqa: BLE001
        checks["can_forward"] = False
        checks["operator_note"] = f"{type(e).__name__}: {e}"
    policy = bort_policy.BortPolicy.load()
    decision = policy.decide(POLICY_ACTION, value_bnb=0.0)
    checks["policy"] = {"mode": policy.mode, "disposition": decision.disposition, "reason": decision.reason}

    result: dict[str, Any] = {
        "token_id":     token_id,
        "vault_id":     vault_id,
        "uri":          uri,
        "source_type":  source_type,
        "source_type_name": KT_NAMES.get(source_type, str(source_type)),
        "priority":     priority,
        "description":  description,
        "content_hash": "0x" + content_hash.hex(),
        "kr_proxy":     kr,
        "calldata":     calldata,
        "preflight":    checks,
    }

    # ---- gate ----
    if checks.get("global_paused") is True:
        result["status"] = "blocked"
        result["reason"] = "system paused via CircuitBreaker"
        return result
    if checks.get("can_forward") is not True:
        result["status"] = "blocked"
        result["reason"] = (
            "operator has no WRITE permission for this agent. Run bort_grant_permission_uri "
            "and have the NFT owner sign the two txs (createVault + grantPermission)."
        )
        return result
    if decision.disposition == "block":
        result["status"] = "blocked"
        result["reason"] = decision.reason or "policy disposition=block"
        return result
    if decision.disposition == "confirm":
        from .approval import request_action_approval, APPROVED, DENIED, GATEWAY_PENDING, NONINTERACTIVE
        verdict = request_action_approval(
            POLICY_ACTION, f"anchor {KT_NAMES.get(source_type, source_type)} knowledge source for agent {token_id}",
        )
        result["approval"] = verdict
        if verdict == DENIED:
            result["status"] = "blocked"
            result["reason"] = "user denied"
            return result
        if verdict == GATEWAY_PENDING:
            result["status"] = "approval_required"
            result["reason"] = "asked the user; re-invoke after they respond via the gateway."
            return result
        if verdict == NONINTERACTIVE:
            result["status"] = "needs_confirm"
            result["reason"] = f"policy requires confirmation; set per_action.{POLICY_ACTION}: auto in ~/.hermes/bort-policy.yaml."
            return result
        # APPROVED → continue

    # ---- simulate ----
    try:
        result["simulate"] = await bort_signer.simulate(kr, bytes.fromhex(calldata[2:]))
    except Exception as e:  # noqa: BLE001
        result["simulate"] = {"skipped": True, "reason": f"{type(e).__name__}: {e}"}

    if os.environ.get("BORT_ALLOW_BROADCAST", "").strip().lower() not in ("1", "true", "yes", "on"):
        result["status"] = "simulated_only"
        result["reason"] = "BORT_ALLOW_BROADCAST not set. Pre-flight + simulate ok; broadcast disabled."
        return result

    # ---- broadcast ----
    try:
        receipt = await bort_signer.sign_and_send(to=kr, data=bytes.fromhex(calldata[2:]))
    except Exception as e:  # noqa: BLE001
        result["status"] = "broadcast_failed"
        result["reason"] = f"{type(e).__name__}: {e}"
        return result
    result["status"]   = "ok" if receipt.ok else "reverted"
    result["tx_hash"]  = receipt.tx_hash
    result["block"]    = receipt.block_number
    result["gas_used"] = receipt.gas_used
    result["operator"] = receipt.operator
    return result
