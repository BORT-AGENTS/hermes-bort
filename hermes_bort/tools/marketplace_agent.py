"""bort_marketplace_agent: marketplace view for one BORT agent: listings, offers, activity."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from .. import bort_api, bort_marketplace


SCHEMA = {
    "name": "bort_marketplace_agent",
    "description": (
        "Marketplace status for a single BORT agent NFT: its active listing(s) and price, "
        "open offers, and recent on-chain marketplace activity (list / sale / offer events). "
        "Read-only. Includes the dapp deep link for the agent's detail page."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "token_id": {"type": "integer", "description": "BAP-578 NFT token ID (e.g. 11100)."},
            "include_offers": {"type": "boolean", "default": True},
            "include_activity": {"type": "boolean", "default": True},
        },
        "required": ["token_id"],
    },
}


def _as_error(x):
    return {"error": f"{type(x).__name__}: {x}"} if isinstance(x, Exception) else x


async def handle(args: dict[str, Any], **kwargs) -> str:
    token_id = int(args["token_id"])
    want_offers = args.get("include_offers", True)
    want_activity = args.get("include_activity", True)

    api = bort_api.client()
    coros = [api.get_marketplace_listings(token_id)]
    if want_offers:
        coros.append(api.get_marketplace_offers(token_id))
    if want_activity:
        coros.append(api.get_marketplace_activity(token_id))
    results = await asyncio.gather(*coros, return_exceptions=True)

    idx = 0
    listings = _as_error(results[idx]) or {}
    idx += 1
    active = (listings or {}).get("active") if isinstance(listings, dict) else None

    payload: dict[str, Any] = {
        "token_id": token_id,
        "detail_url": bort_marketplace.listing_url(token_id),
        "marketplace_contract": bort_marketplace.MARKETPLACE_V3,
        "is_listed": bool(active),
        "active_listings": active,
        "all_listings": (listings or {}).get("items") if isinstance(listings, dict) else listings,
    }
    if want_offers:
        offers = _as_error(results[idx]) or {}
        idx += 1
        payload["offers"] = offers.get("items") if isinstance(offers, dict) else offers
    if want_activity:
        activity = _as_error(results[idx]) or {}
        idx += 1
        payload["activity"] = activity.get("items") if isinstance(activity, dict) else activity
    return json.dumps(payload, ensure_ascii=False)


def register_marketplace_agent(ctx) -> None:
    ctx.register_tool(
        name="bort_marketplace_agent",
        toolset="bort",
        schema=SCHEMA,
        handler=handle,
        is_async=True,
        description="Marketplace listing / offers / activity for one BORT agent.",
        emoji="📈",
    )
