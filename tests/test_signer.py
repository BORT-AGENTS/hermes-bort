"""Unit tests for the operator-key signer. No real broadcasts."""
from __future__ import annotations

import pytest

from hermes_bort import bort_signer


# Anvil's first deterministic account: pure test key, NEVER used for real funds.
TEST_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_ADDR = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


def test_operator_key_missing_raises(monkeypatch):
    monkeypatch.delenv("BORT_OPERATOR_PRIVATE_KEY", raising=False)
    with pytest.raises(bort_signer.OperatorKeyMissing):
        bort_signer.operator_address()


def test_operator_address_returns_checksum_from_env(monkeypatch):
    monkeypatch.setenv("BORT_OPERATOR_PRIVATE_KEY", TEST_KEY)
    addr = bort_signer.operator_address()
    assert addr == TEST_ADDR


def test_operator_key_accepts_unprefixed_hex(monkeypatch):
    monkeypatch.setenv("BORT_OPERATOR_PRIVATE_KEY", TEST_KEY[2:])  # strip 0x
    addr = bort_signer.operator_address()
    assert addr == TEST_ADDR


def test_invalid_key_raises_clear_error(monkeypatch):
    monkeypatch.setenv("BORT_OPERATOR_PRIVATE_KEY", "0xnotahex")
    with pytest.raises(bort_signer.BortSignerError):
        bort_signer.operator_address()


def test_sign_and_send_refuses_without_broadcast_flag(monkeypatch):
    """Critical safety check: real broadcasts require BORT_ALLOW_BROADCAST=1."""
    import asyncio
    monkeypatch.setenv("BORT_OPERATOR_PRIVATE_KEY", TEST_KEY)
    monkeypatch.delenv("BORT_ALLOW_BROADCAST", raising=False)

    async def run():
        with pytest.raises(bort_signer.BortSignerError, match="BORT_ALLOW_BROADCAST"):
            await bort_signer.sign_and_send(
                to="0x0000000000000000000000000000000000000000",
                data=b"\x00" * 32,
            )

    asyncio.run(run())
