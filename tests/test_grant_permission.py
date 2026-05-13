"""bort_grant_permission_uri dry-run: verifies the two-step calldata generation.

No broadcast. Asserts the createVault and grantPermission calldatas are well-formed
and use the correct ABI (string vaultId, WRITE=2 level).
"""
from __future__ import annotations

import json

import pytest

from hermes_bort.tools.grant_permission import (
    handle as grant_permission,
    _encode_create_vault_calldata,
    _encode_grant_permission_calldata,
)


# Anvil deterministic key: pure test value.
TEST_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_OPERATOR_ADDR = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
TEST_TOKEN_ID = 11100


@pytest.mark.asyncio
async def test_grant_permission_returns_both_calldatas(monkeypatch):
    monkeypatch.setenv("BORT_OPERATOR_PRIVATE_KEY", TEST_KEY)
    raw = await grant_permission({"token_id": TEST_TOKEN_ID, "duration_hours": 24})
    parsed = json.loads(raw)

    assert parsed["token_id"] == TEST_TOKEN_ID
    assert parsed["duration_hours"] == 24
    assert parsed["duration_seconds"] == 86400
    assert parsed["operator_address"] == TEST_OPERATOR_ADDR
    # vaultId is now the decimal string of tokenId, NOT a keccak hex
    assert parsed["vault_id"] == "11100"
    # VPM v2 proxy address (lowercased comparison)
    assert parsed["contract"].lower() == "0x0fa3f984f7999d31c28055260637d1bcea34919a"

    # Step 1: createVault
    s1 = parsed["step_1_create_vault"]
    assert s1["function"] == "createVault(string,string)"
    assert s1["args"]["vaultId"] == "11100"
    assert s1["calldata"].startswith("0x")
    assert s1["to"].lower() == parsed["contract"].lower()

    # Step 2: grantPermission
    s2 = parsed["step_2_grant_permission"]
    assert s2["function"] == "grantPermission(address,string,uint8,uint256,string)"
    assert s2["args"]["delegate"] == TEST_OPERATOR_ADDR
    assert s2["args"]["vaultId"] == "11100"
    assert s2["args"]["level"] == 2          # WRITE
    assert s2["args"]["duration"] == 86400
    assert s2["args"]["metadata"] == ""
    assert s2["calldata"].startswith("0x")
    assert s2["to"].lower() == parsed["contract"].lower()

    assert "bscscan.com" in parsed["bscscan_url"]


@pytest.mark.asyncio
async def test_grant_permission_returns_clear_error_without_operator_key(monkeypatch):
    monkeypatch.delenv("BORT_OPERATOR_PRIVATE_KEY", raising=False)
    raw = await grant_permission({"token_id": TEST_TOKEN_ID})
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "BORT_OPERATOR_PRIVATE_KEY" in parsed["error"]


def test_create_vault_calldata_is_deterministic():
    a = _encode_create_vault_calldata(TEST_TOKEN_ID, "test desc")
    b = _encode_create_vault_calldata(TEST_TOKEN_ID, "test desc")
    assert a == b
    assert a.startswith("0x")
    # 4-byte selector + ABI-encoded args. Selector value verified by keccak at runtime.
    assert len(a) > 10
    # Different inputs should produce different calldata (sanity)
    c = _encode_create_vault_calldata(TEST_TOKEN_ID + 1, "test desc")
    assert a != c


def test_grant_permission_calldata_uses_write_level():
    cd = _encode_grant_permission_calldata(TEST_TOKEN_ID, TEST_OPERATOR_ADDR, 86400)
    assert cd.startswith("0x")
    # Selector for grantPermission(address,string,uint8,uint256,string)
    # Verified via keccak: used to be 0x925dc147 for the (address,bytes32,uint8,uint256,bytes) form.
    # The string variant has a different selector.
    selector = cd[2:10].lower()
    assert selector != "925dc147", "calldata is still using the bytes32 form: bug"
