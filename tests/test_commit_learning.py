"""bort_commit_learning dry-run integration test against BSC mainnet.

Exercises the full path EXCEPT actual broadcast: pre-flight reads via canForward,
simulate via eth_call, policy check, calldata encoding for VPMv2.forwardHandleAction.
No real tx ever sent: BORT_ALLOW_BROADCAST is intentionally NOT set in these tests.
"""
from __future__ import annotations

import json
import os

import pytest

from hermes_bort.tools.commit_learning import (
    handle as commit_learning,
    _encode_forward_handle_action_calldata,
    _encode_record_learning_payload,
    SEL_FORWARD_HANDLE_ACTION,
)


TEST_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_TOKEN_ID = int(os.environ.get("BORT_TEST_TOKEN_ID", "11100"))
SAMPLE_HASH = "0x" + "11" * 32  # bytes32 of 0x11 repeated


@pytest.mark.asyncio
async def test_commit_learning_routes_via_vpm_v2_and_blocks_without_permission(monkeypatch):
    """Without the operator having been granted WRITE permission on VPM v2,
    canForward returns false and the tool refuses to broadcast: even in dry-run."""
    monkeypatch.delenv("BORT_ALLOW_BROADCAST", raising=False)
    monkeypatch.setenv("BORT_OPERATOR_PRIVATE_KEY", TEST_KEY)

    raw = await commit_learning({
        "token_id":          TEST_TOKEN_ID,
        "data_hash":         SAMPLE_HASH,
        "interaction_count": 1,
    })
    parsed = json.loads(raw)

    assert parsed["token_id"] == TEST_TOKEN_ID
    assert parsed["action"] == "record_learning"
    assert parsed["vault_id"] == str(TEST_TOKEN_ID)
    assert parsed["calldata"].startswith("0x")
    # Calldata must target VPM v2's forwardHandleAction selector (the bridge),
    # NOT the logic's direct handleAction selector. This is the fix from earlier.
    assert parsed["calldata"][2:10].lower() == SEL_FORWARD_HANDLE_ACTION[2:].lower()

    # VPM v2 proxy as the target
    assert parsed["vpm_proxy"].lower() == "0x0fa3f984f7999d31c28055260637d1bcea34919a"

    pf = parsed["preflight"]
    assert "global_paused" in pf
    assert "logic_paused" in pf
    assert "can_forward" in pf
    assert "policy" in pf
    # The Anvil test operator has never been granted permission on agent 11100 → can_forward False
    assert pf.get("can_forward") is False
    assert parsed["status"] == "blocked"
    assert "WRITE permission" in parsed["reason"]


@pytest.mark.asyncio
async def test_commit_learning_rejects_bad_hash():
    raw = await commit_learning({
        "token_id":  TEST_TOKEN_ID,
        "data_hash": "0xshort",
    })
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "32 bytes" in parsed["error"]


def test_record_learning_payload_encoding_round_trip():
    """encode(bytes32, uint256): confirm 64-byte size."""
    payload = _encode_record_learning_payload(b"\x42" * 32, 5)
    assert len(payload) == 64  # 32 bytes for bytes32 + 32 bytes for uint256


def test_forward_handle_action_calldata_prefix_is_correct_selector():
    cd = _encode_forward_handle_action_calldata(
        token_id=11100,
        vault_id="11100",
        logic_address="0x4F35D6B3DEdecfe3aD6600b39A705BcD53E2aE81",
        action="record_learning",
        inner_payload=b"\x00" * 64,
    )
    assert cd.startswith("0x")
    assert cd[2:10].lower() == SEL_FORWARD_HANDLE_ACTION[2:].lower()
    assert len(cd) > 10
