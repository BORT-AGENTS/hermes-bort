"""bort_health_check: CircuitBreaker pre-flight."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from .. import bort_chain
from ..bort_chain import ADDRESSES


SCHEMA = {
    "name": "bort_health_check",
    "description": (
        "Check BORT system health on BSC mainnet. Returns CircuitBreaker.globalPause and "
        "per-contract pause status for the active logic contracts (Hunter, Trading V5, CTO). "
        "Mandatory pre-flight before issuing any write. Cheap: just a few view calls."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}


async def handle(args: dict[str, Any], **kwargs) -> str:
    targets = ["BAP578", "HunterLogic", "TradingLogicV5", "CTOLogic"]
    coros = [bort_chain.get_global_pause()] + [
        bort_chain.is_contract_paused(ADDRESSES[k]) for k in targets
    ]
    results = await asyncio.gather(*coros, return_exceptions=True)

    def _v(x):
        if isinstance(x, Exception):
            return {"error": f"{type(x).__name__}: {x}"}
        return x

    global_paused = _v(results[0])
    contracts = {k: _v(v) for k, v in zip(targets, results[1:])}
    ok = (
        isinstance(global_paused, bool) and not global_paused
        and all(isinstance(v, bool) and not v for v in contracts.values())
    )
    response = {
        "global_paused": global_paused,
        "contracts":     contracts,
        "ok_to_write":   ok,
    }
    return json.dumps(response, ensure_ascii=False)


def register_health_check(ctx) -> None:
    ctx.register_tool(
        name="bort_health_check",
        toolset="bort",
        schema=SCHEMA,
        handler=handle,
        is_async=True,
        description="CircuitBreaker pre-flight for BORT mainnet.",
        emoji="🚦",
    )
