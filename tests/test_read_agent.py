"""Integration test against BSC mainnet. Override target via BORT_TEST_TOKEN_ID env (default 11100)."""
from __future__ import annotations

import json
import os

import pytest

from hermes_bort.tools.read_agent import handle as read_agent


TEST_TOKEN_ID = int(os.environ.get("BORT_TEST_TOKEN_ID", "11100"))


@pytest.mark.asyncio
async def test_read_agent_returns_structured_response():
    raw = await read_agent({"token_id": TEST_TOKEN_ID})
    parsed = json.loads(raw)

    assert parsed["token_id"] == TEST_TOKEN_ID
    assert "on_chain" in parsed
    assert "logic" in parsed

    state = parsed["on_chain"]["state"]
    assert state is not None, "BAP-578 getState should succeed for a real tokenId"
    assert state["owner"], "owner should be populated"
    assert state["logic_name"] in {"Hunter", "Trading V5", "CTO", None}
    assert state["status"] in {"Paused", "Active", "Terminated"}


@pytest.mark.asyncio
async def test_read_agent_lean_mode_skips_logic_and_trades():
    raw = await read_agent(
        {"token_id": TEST_TOKEN_ID, "include_logic_details": False, "include_trades": False}
    )
    parsed = json.loads(raw)
    assert parsed["logic"] is None
    assert parsed["trades"] is None
    assert parsed["on_chain"]["state"] is not None


@pytest.mark.asyncio
async def test_read_agent_includes_token_uri_when_present():
    raw = await read_agent({"token_id": TEST_TOKEN_ID})
    parsed = json.loads(raw)
    token_uri = parsed["on_chain"].get("token_uri")
    # tokenURI may be empty for some agents; if it's set, identity fetch should attempt
    if token_uri:
        assert isinstance(token_uri, str)
