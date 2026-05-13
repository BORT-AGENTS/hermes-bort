"""bort_grant_permission_uri: generate the deep-links the NFT owner clicks to
authorize Hermes' operator key for an agent.

The VaultPermissionManager v2 contract requires TWO owner-signed transactions to
authorize Hermes for a given agent:

  1. createVault(vaultId, description)          : establishes a permission namespace
  2. grantPermission(operator, vaultId, WRITE,  : gives the Hermes operator key
                     duration, metadata)            time-bounded WRITE access

If the vault already exists for the owner+vaultId pair, step 1 reverts with
"vault already exists": that's fine, skip it and go straight to step 2.

Convention: vaultId is `str(tokenId)`. The contract doesn't enforce this; it's
chosen for predictability (Hermes plugin always uses the same string for a given
agent so checkPermission lookups don't need a separate mapping).

After both txs confirm, the Hermes operator can call
`VPMv2.forwardHandleAction(tokenId, str(tokenId), logicAddr, action, payload)`
for `duration_hours` and the call will be routed to the agent's logic contract.
"""
from __future__ import annotations

import json
from typing import Any

from eth_abi import encode as abi_encode
from eth_utils import keccak
from web3 import AsyncWeb3

from .. import bort_signer
from ..bort_chain import ADDRESSES, PERMISSION_LEVEL


# Selectors derived from the actual deployed VPM v2 signatures (strings, not bytes32).
SEL_CREATE_VAULT = "0x" + keccak(
    text="createVault(string,string)"
).hex()[:8]

SEL_GRANT_PERMISSION = "0x" + keccak(
    text="grantPermission(address,string,uint8,uint256,string)"
).hex()[:8]


SCHEMA = {
    "name": "bort_grant_permission_uri",
    "description": (
        "Generate the two transactions the NFT owner signs from their wallet to "
        "authorize Hermes' operator key for an agent: (1) createVault to establish a "
        "permission namespace, (2) grantPermission to give the operator WRITE access "
        "for the given duration. Returns calldata + BSCScan write-page link for each. "
        "Does NOT broadcast: owner signs from MetaMask. After confirmation, Hermes "
        "can call forwardHandleAction on the agent's logic contract."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "token_id": {"type": "integer", "description": "BAP-578 NFT token ID."},
            "duration_hours": {
                "type": "integer",
                "default": 24,
                "description": "How long the WRITE permission stays valid. Default 24. Short is safer.",
            },
            "vault_description": {
                "type": "string",
                "default": "BORT agent Hermes operator vault",
                "description": "Free-form description stored in the vault record. Optional.",
            },
        },
        "required": ["token_id"],
    },
}


def _encode_create_vault_calldata(token_id: int, description: str) -> str:
    vault_id = str(int(token_id))  # canonical: decimal string of tokenId
    args = abi_encode(["string", "string"], [vault_id, description])
    return SEL_CREATE_VAULT + args.hex()


def _encode_grant_permission_calldata(
    token_id: int, operator_addr: str, duration_seconds: int, metadata: str = ""
) -> str:
    vault_id = str(int(token_id))
    operator = AsyncWeb3.to_checksum_address(operator_addr)
    args = abi_encode(
        ["address", "string", "uint8", "uint256", "string"],
        [operator, vault_id, PERMISSION_LEVEL["WRITE"], duration_seconds, metadata],
    )
    return SEL_GRANT_PERMISSION + args.hex()


def _bscscan_write_url(vpm_addr: str) -> str:
    return f"https://bscscan.com/address/{vpm_addr}#writeContract"


async def handle(args: dict[str, Any], **kwargs) -> str:
    token_id = int(args["token_id"])
    duration_hours = int(args.get("duration_hours", 24))
    description = str(args.get("vault_description", "BORT agent Hermes operator vault"))
    duration_seconds = duration_hours * 3600

    try:
        operator_addr = bort_signer.operator_address()
    except bort_signer.OperatorKeyMissing as e:
        return json.dumps({"error": str(e), "tool": "bort_grant_permission_uri"})

    vpm_addr = ADDRESSES["VaultPermissionManager"]
    vault_id = str(token_id)

    create_calldata = _encode_create_vault_calldata(token_id, description)
    grant_calldata = _encode_grant_permission_calldata(
        token_id, operator_addr, duration_seconds, metadata=""
    )

    response = {
        "token_id":         token_id,
        "vault_id":         vault_id,
        "duration_hours":   duration_hours,
        "duration_seconds": duration_seconds,
        "operator_address": operator_addr,
        "contract":         vpm_addr,
        "bscscan_url":      _bscscan_write_url(vpm_addr),
        "step_1_create_vault": {
            "purpose":   "Establish the vault namespace. Skip this if you've granted permission for this agent before: the vault already exists.",
            "function":  "createVault(string,string)",
            "args": {
                "vaultId":     vault_id,
                "description": description,
            },
            "calldata":  create_calldata,
            "to":        vpm_addr,
        },
        "step_2_grant_permission": {
            "purpose":  "Grant the Hermes operator key WRITE permission on the agent's vault for the given duration.",
            "function": "grantPermission(address,string,uint8,uint256,string)",
            "args": {
                "delegate": operator_addr,
                "vaultId":  vault_id,
                "level":    PERMISSION_LEVEL["WRITE"],
                "duration": duration_seconds,
                "metadata": "",
            },
            "calldata": grant_calldata,
            "to":       vpm_addr,
        },
        "instructions": (
            "1. Open the bscscan_url above. Connect your MetaMask to the NFT owner account. "
            "2. Find `createVault`, fill vaultId + description, sign. (Skip if it reverts with "
            "'vault already exists': your vault is already there.) "
            "3. Find `grantPermission`, fill the args from step_2_grant_permission above, sign. "
            "4. Once both txs confirm, Hermes can act on this agent for "
            f"{duration_hours} hours under WRITE permission. Revoke anytime via revokePermission."
        ),
        "safety_note": (
            "WRITE permission lets the Hermes operator call any handleAction the agent's "
            "logic contract supports (trades, position management, learning). The "
            "duration is the upper bound. Keep it short and revoke when not in active use."
        ),
    }
    return json.dumps(response, ensure_ascii=False)


def register_grant_permission(ctx) -> None:
    ctx.register_tool(
        name="bort_grant_permission_uri",
        toolset="bort",
        schema=SCHEMA,
        handler=handle,
        is_async=True,
        description="Generate createVault + grantPermission deep-links for the NFT owner to authorize Hermes.",
        emoji="🔑",
    )
