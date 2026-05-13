"""Operator key load + transaction signing for BORT writes on BSC mainnet.

The operator key lives in the env var ``BORT_OPERATOR_PRIVATE_KEY``. The plugin
never sees the NFT owner's key: the owner authorizes the operator via a one-time
on-chain ``VaultPermissionManager.grantPermission(operator_addr, EXECUTE, ...)``
tx signed from their own wallet (see tools/grant_permission.py).

Safety boundary: the operator key can ONLY do what the policy file allows
AND what the agent's vault permission grants. Even with the key, a thief
gets only the granted-EXECUTE scope, time-bounded, with per-action BNB caps.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import AsyncWeb3
from web3.types import TxParams

from .bort_chain import web3


CHAIN_ID = 56  # BSC mainnet

DEFAULT_GAS_LIMIT = 600_000   # generous; handleAction can be expensive on Hunter/CTO
RECEIPT_TIMEOUT_S = 180
RECEIPT_POLL_INTERVAL_S = 2


class BortSignerError(RuntimeError):
    """Raised when signing or broadcast fails."""


class OperatorKeyMissing(BortSignerError):
    """Raised when BORT_OPERATOR_PRIVATE_KEY is not set."""


@dataclass(frozen=True)
class TxReceipt:
    """Subset of the eth receipt we surface to tools."""
    tx_hash: str
    status: int             # 1 = success, 0 = reverted
    block_number: int
    gas_used: int
    operator: str
    from_address: str
    to_address: str
    raw: dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.status == 1


def _load_operator() -> LocalAccount:
    """Load the operator key from env. Returns an eth_account LocalAccount."""
    raw = os.environ.get("BORT_OPERATOR_PRIVATE_KEY", "").strip()
    if not raw:
        raise OperatorKeyMissing(
            "BORT_OPERATOR_PRIVATE_KEY env var is not set. "
            "Generate one with `python -c \"from eth_account import Account; "
            "print(Account.create().key.hex())\"` and export it before "
            "starting Hermes. Then fund the resulting address with a small "
            "amount of BNB for gas."
        )
    if not raw.startswith("0x"):
        raw = "0x" + raw
    try:
        return Account.from_key(raw)
    except Exception as e:  # noqa: BLE001
        raise BortSignerError(f"BORT_OPERATOR_PRIVATE_KEY invalid: {e}") from e


def operator_address() -> str:
    """Return the operator's checksum address. Raises if env var unset."""
    return _load_operator().address


async def operator_balance_bnb() -> float:
    """Return current BNB balance of the operator account."""
    w3 = web3()
    addr = AsyncWeb3.to_checksum_address(operator_address())
    wei = await w3.eth.get_balance(addr)
    return float(AsyncWeb3.from_wei(int(wei), "ether"))


async def build_tx(to: str, data: bytes, *, value_wei: int = 0, gas_limit: int | None = None) -> TxParams:
    """Build a TxParams dict with sensible defaults (chain id, nonce, fees)."""
    w3 = web3()
    operator = _load_operator()
    nonce = await w3.eth.get_transaction_count(operator.address, "pending")
    gas_price = await w3.eth.gas_price
    return {
        "to":       AsyncWeb3.to_checksum_address(to),
        "from":     operator.address,
        "data":     data if isinstance(data, (bytes, bytearray)) else bytes.fromhex(data.replace("0x", "")),
        "value":    int(value_wei),
        "gas":      int(gas_limit or DEFAULT_GAS_LIMIT),
        "gasPrice": int(gas_price),
        "nonce":    int(nonce),
        "chainId":  CHAIN_ID,
    }


async def simulate(to: str, data: bytes, *, value_wei: int = 0) -> dict[str, Any]:
    """Dry-run via eth_call. Returns {ok, result_hex, error}.

    Use before broadcasting to catch contract reverts without spending gas.
    """
    w3 = web3()
    operator = _load_operator()
    call_obj = {
        "to":    AsyncWeb3.to_checksum_address(to),
        "from":  operator.address,
        "data":  "0x" + (data.hex() if isinstance(data, (bytes, bytearray)) else data.replace("0x", "")),
        "value": hex(int(value_wei)),
    }
    try:
        result = await w3.eth.call(call_obj)
        return {"ok": True, "result_hex": "0x" + result.hex(), "error": None}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "result_hex": None, "error": f"{type(e).__name__}: {e}"}


async def sign_and_send(to: str, data: bytes, *, value_wei: int = 0,
                       gas_limit: int | None = None,
                       wait: bool = True) -> TxReceipt:
    """Sign the tx with the operator key and broadcast on BSC.

    If wait=True, polls for receipt. Returns a TxReceipt either way; if not waiting,
    .status will be -1 indicating "submitted, not yet mined".

    Pre-flight expectation: callers should run simulate() first and check
    policy + on-chain pre-flight (CircuitBreaker, VaultPermissionManager).
    This function does NOT enforce policy: it just signs and broadcasts.
    """
    if os.environ.get("BORT_ALLOW_BROADCAST", "").strip().lower() not in (
        "1", "true", "yes", "on",
    ):
        raise BortSignerError(
            "BORT_ALLOW_BROADCAST is not set. As a safety default, real broadcasts are "
            "disabled. Set BORT_ALLOW_BROADCAST=1 in the environment to enable "
            "actual on-chain writes."
        )

    w3 = web3()
    operator = _load_operator()
    tx = await build_tx(to, data, value_wei=value_wei, gas_limit=gas_limit)
    signed = operator.sign_transaction(tx)
    tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
    tx_hash_hex = "0x" + tx_hash.hex()

    if not wait:
        return TxReceipt(
            tx_hash=tx_hash_hex, status=-1, block_number=0, gas_used=0,
            operator=operator.address, from_address=operator.address,
            to_address=tx["to"], raw={"submitted": True},
        )

    receipt = await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=RECEIPT_TIMEOUT_S,
                                                       poll_latency=RECEIPT_POLL_INTERVAL_S)
    return TxReceipt(
        tx_hash=tx_hash_hex,
        status=int(receipt.status),
        block_number=int(receipt.blockNumber),
        gas_used=int(receipt.gasUsed),
        operator=operator.address,
        from_address=operator.address,
        to_address=tx["to"],
        raw=dict(receipt),
    )
