"""bort_marketplace_browse: list active BORT agent listings + recent sales."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from .. import bort_api, bort_marketplace


SCHEMA = {
    "name": "bort_marketplace_browse",
    "description": (
        "Browse the BORT agent marketplace (MarketplaceV3 on BSC mainnet). Returns active "
        "listings (token id, price in BNB, seller, listing id, currency) and the most recent "
        "sales. Read-only via the BORT runtime API: no wallet needed. To actually buy or list, "
        "use bort_list_agent_uri or the dapp deep links it returns."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "sort": {
                "type": "string",
                "enum": ["recent", "price_asc", "price_desc"],
                "default": "recent",
                "description": "Ordering for the listings page.",
            },
            "limit": {"type": "integer", "default": 25, "minimum": 1, "maximum": 200},
            "offset": {"type": "integer", "default": 0, "minimum": 0},
            "seller": {
                "type": "string",
                "description": "Optional 0x address: only listings created by this seller.",
            },
            "include_recent_sales": {"type": "boolean", "default": True},
        },
        "required": [],
    },
}


def _as_error(x):
    return {"error": f"{type(x).__name__}: {x}"} if isinstance(x, Exception) else x


async def handle(args: dict[str, Any], **kwargs) -> str:
    sort = args.get("sort", "recent")
    limit = int(args.get("limit", 25))
    offset = int(args.get("offset", 0))
    seller = args.get("seller")
    want_sales = args.get("include_recent_sales", True)

    api = bort_api.client()
    coros = [api.list_marketplace_listings(sort=sort, limit=limit, offset=offset, lister=seller)]
    if want_sales:
        coros.append(api.get_marketplace_sales_recent(limit=min(limit, 25)))
    results = await asyncio.gather(*coros, return_exceptions=True)

    listings = _as_error(results[0]) or {}
    payload: dict[str, Any] = {
        "marketplace_contract": bort_marketplace.MARKETPLACE_V3,
        "browse_url": bort_marketplace.seller_url(seller) if seller else bort_marketplace.marketplace_url(),
        "sort": sort,
        "total": (listings or {}).get("total") if isinstance(listings, dict) else None,
        "listings": (listings or {}).get("items") if isinstance(listings, dict) else listings,
    }
    if want_sales:
        sales = _as_error(results[1]) or {}
        payload["recent_sales"] = (sales or {}).get("items") if isinstance(sales, dict) else sales
    return json.dumps(payload, ensure_ascii=False)


def register_marketplace_browse(ctx) -> None:
    ctx.register_tool(
        name="bort_marketplace_browse",
        toolset="bort",
        schema=SCHEMA,
        handler=handle,
        is_async=True,
        description="Browse BORT agent marketplace listings + recent sales.",
        emoji="🏷️",
    )
