"""Health check integration test against BSC mainnet."""
from __future__ import annotations

import json

import pytest

from hermes_bort.tools.health_check import handle as health_check


@pytest.mark.asyncio
async def test_health_check_returns_circuit_state():
    raw = await health_check({})
    parsed = json.loads(raw)
    assert "global_paused" in parsed
    assert "contracts" in parsed
    assert "ok_to_write" in parsed
    assert isinstance(parsed["ok_to_write"], bool)
    # All four target contracts should appear in the result
    for k in ("BAP578", "HunterLogic", "TradingLogicV5", "CTOLogic"):
        assert k in parsed["contracts"]
