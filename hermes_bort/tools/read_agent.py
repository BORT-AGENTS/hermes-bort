"""bort_read_agent: multi-source merged read of a BORT agent."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from .. import bort_api, bort_chain, bort_ipfs
from ..bort_logic_adapters import get_adapter
from ..bort_sanitize import wrap_fields, wrap_external


SCHEMA = {
    "name": "bort_read_agent",
    "description": (
        "Read a BORT (BAP-578) agent NFT's full state. Returns on-chain BAP-578 fields, "
        "IPFS identity (name, image, description), soul status, recent trades and PnL, "
        "knowledge sources, learning metrics, and logic-specific state (Hunter positions, "
        "CTO campaign, etc.). If a sub-source is unavailable that field is null but other "
        "sources still return."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "token_id": {
                "type": "integer",
                "description": "BAP-578 NFT token ID (e.g. 11100).",
            },
            "include_logic_details": {
                "type": "boolean",
                "default": True,
                "description": "Include per-logic-contract state (positions / campaign / vault).",
            },
            "include_trades": {
                "type": "boolean",
                "default": True,
                "description": "Include trade summary and PnL from runtime API.",
            },
        },
        "required": ["token_id"],
    },
}


async def _safe(coro):
    try:
        return await coro
    except Exception as e:  # noqa: BLE001
        return {"__error__": f"{type(e).__name__}: {e}"}


def _unwrap(value):
    if isinstance(value, dict) and "__error__" in value:
        return None
    return value


async def handle(args: dict[str, Any], **kwargs) -> str:
    token_id = int(args["token_id"])
    include_logic = bool(args.get("include_logic_details", True))
    include_trades = bool(args.get("include_trades", True))

    api = bort_api.client()

    chain_tasks = [
        _safe(bort_chain.get_state(token_id)),
        _safe(bort_chain.get_agent_metadata(token_id)),
        _safe(bort_chain.get_token_uri(token_id)),
        _safe(bort_chain.get_knowledge_sources(token_id, active_only=True)),
    ]
    api_tasks = [
        _safe(api.get_soul_status(token_id)),
        _safe(api.find_in_leaderboard(token_id)),
    ]
    if include_trades:
        api_tasks += [
            _safe(api.get_trade_summary(token_id)),
            _safe(api.get_trade_pnl(token_id)),
        ]

    results = await asyncio.gather(*chain_tasks, *api_tasks)
    state         = _unwrap(results[0])
    metadata      = _unwrap(results[1])
    token_uri_raw = _unwrap(results[2])
    knowledge     = _unwrap(results[3]) or []
    soul          = _unwrap(results[4])
    leaderboard   = _unwrap(results[5])
    if include_trades:
        trades = _unwrap(results[6])
        pnl = _unwrap(results[7])
    else:
        trades = None
        pnl = None

    identity: dict[str, Any] | None = None
    if isinstance(token_uri_raw, str) and token_uri_raw:
        identity = await bort_ipfs.fetch_json(token_uri_raw)
        # Owner-controlled free-form text from IPFS: mark as untrusted data.
        if isinstance(identity, dict):
            identity = wrap_fields(
                identity,
                source=f"ipfs-identity:token-{token_id}",
                keys=("name", "description", "external_url"),
            )

    # Knowledge source descriptions are owner-set on-chain text. Wrap them.
    if isinstance(knowledge, list):
        knowledge = [
            wrap_fields(src, source=f"knowledge-registry:token-{token_id}", keys=("description",))
            if isinstance(src, dict) else src
            for src in knowledge
        ]

    logic_data: dict[str, Any] | None = None
    if include_logic and isinstance(state, dict):
        adapter = get_adapter(state.get("logic_address"))
        if adapter is not None:
            try:
                logic_data = await adapter.read(token_id)
            except Exception as e:  # noqa: BLE001
                logic_data = {"error": f"{type(e).__name__}: {e}"}

    response: dict[str, Any] = {
        "token_id": token_id,
        "on_chain": {
            "state":     state,
            "metadata":  metadata,
            "token_uri": token_uri_raw if isinstance(token_uri_raw, str) else None,
        },
        "identity":  identity,
        "soul":      soul,
        "metrics":   leaderboard,
        "knowledge": knowledge,
        "logic":     logic_data,
        "trades": {
            "summary": trades,
            "pnl":     pnl,
        } if include_trades else None,
    }
    return json.dumps(response, default=str, ensure_ascii=False)


def register_read_agent(ctx) -> None:
    ctx.register_tool(
        name="bort_read_agent",
        toolset="bort",
        schema=SCHEMA,
        handler=handle,
        is_async=True,
        description="Read full state of a BORT agent NFT.",
        emoji="🤖",
    )
