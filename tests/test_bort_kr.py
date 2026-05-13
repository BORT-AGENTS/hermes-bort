"""Tests for hermes_bort.bort_kr: the KR v2 delegated-write helper.

Calldata builder is pure-unit. write_knowledge_source_delegated is exercised against
live chain reads (CircuitBreaker, VPMv2.canForward) but never broadcasts: in the test
env there's no real operator permission, so it blocks before the broadcast path.
"""
from __future__ import annotations

import json
import os

import pytest

from hermes_bort import bort_kr


TEST_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_TOKEN_ID = int(os.environ.get("BORT_TEST_TOKEN_ID", "11100"))


def test_kt_constants():
    assert bort_kr.KT_MEMORY == 2
    assert bort_kr.KT_INSTRUCTION == 3
    assert bort_kr.KT_NAMES[2] == "MEMORY"
    assert bort_kr.KT_NAMES[3] == "INSTRUCTION"


def test_encode_calldata_deterministic_and_has_selector():
    a = bort_kr.encode_add_delegated_calldata(
        11100, "11100", "ipfs://abc", bort_kr.KT_MEMORY, 100, "session memory", b"\x11" * 32,
    )
    b = bort_kr.encode_add_delegated_calldata(
        11100, "11100", "ipfs://abc", bort_kr.KT_MEMORY, 100, "session memory", b"\x11" * 32,
    )
    assert a == b
    assert a.startswith("0x")
    assert a[2:10].lower() == bort_kr.SEL_ADD_DELEGATED[2:].lower()
    # different inputs → different calldata
    c = bort_kr.encode_add_delegated_calldata(
        11101, "11101", "ipfs://abc", bort_kr.KT_MEMORY, 100, "session memory", b"\x11" * 32,
    )
    assert a != c


@pytest.mark.asyncio
async def test_write_blocks_without_operator_key(monkeypatch):
    monkeypatch.delenv("BORT_OPERATOR_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("BORT_ALLOW_BROADCAST", raising=False)
    result = await bort_kr.write_knowledge_source_delegated(
        TEST_TOKEN_ID, "ipfs://test-cid", bort_kr.KT_MEMORY, priority=10, description="x",
    )
    assert result["status"] == "blocked"
    assert "WRITE permission" in result["reason"]
    # calldata is still built (useful for inspection)
    assert result["calldata"].startswith("0x")
    assert result["kr_proxy"].lower() == "0xb8e808f7916a53c595a0740e656c8bf05388e29a"


@pytest.mark.asyncio
async def test_write_blocks_with_operator_but_no_real_permission(monkeypatch):
    monkeypatch.setenv("BORT_OPERATOR_PRIVATE_KEY", TEST_KEY)
    monkeypatch.delenv("BORT_ALLOW_BROADCAST", raising=False)
    result = await bort_kr.write_knowledge_source_delegated(
        TEST_TOKEN_ID, "ipfs://test-cid", bort_kr.KT_INSTRUCTION, priority=50, description="evolved skill",
    )
    # The test operator (Anvil key) has no WRITE grant on agent 11100's vault → can_forward False
    assert result["preflight"].get("can_forward") is False
    assert result["status"] == "blocked"


@pytest.mark.asyncio
async def test_write_simulated_only_when_permission_mocked_but_broadcast_off(monkeypatch):
    monkeypatch.setenv("BORT_OPERATOR_PRIVATE_KEY", TEST_KEY)
    monkeypatch.delenv("BORT_ALLOW_BROADCAST", raising=False)

    async def fake_can_forward(token_id, vault_id, accessor):  # noqa: ARG001
        return True
    monkeypatch.setattr("hermes_bort.bort_chain.can_forward", fake_can_forward)

    result = await bort_kr.write_knowledge_source_delegated(
        TEST_TOKEN_ID, "ipfs://test-cid", bort_kr.KT_MEMORY, priority=10, description="x",
    )
    # Permission passes, policy for addKnowledgeSource is `auto`, but broadcast disabled
    assert result["status"] == "simulated_only"
    assert "BORT_ALLOW_BROADCAST" in result["reason"]
    assert "simulate" in result


@pytest.mark.asyncio
async def test_write_rejects_bad_content_hash():
    result = await bort_kr.write_knowledge_source_delegated(
        TEST_TOKEN_ID, "ipfs://x", bort_kr.KT_MEMORY, content_hash=b"\x00" * 16,  # wrong length
    )
    assert result["status"] == "blocked"
    assert "32 bytes" in result["reason"]
