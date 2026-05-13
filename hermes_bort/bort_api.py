"""Async client for the BORT runtime API at bap578-nfa-platform.onrender.com.

Owns the public read endpoints. All return parsed JSON or None on failure. Read
endpoints are CORS-enabled, no auth required. Rate limit is 100 req / 15min global.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import httpx


DEFAULT_BASE_URL = "https://bap578-nfa-platform.onrender.com"
DEFAULT_TIMEOUT = 15.0


def _base_url() -> str:
    return os.environ.get("BORT_API_URL", DEFAULT_BASE_URL).rstrip("/")


class BortApiClient:
    """Lightweight per-call client. Avoids stale connection pools across event loops in tests."""

    def __init__(self, base_url: str | None = None, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = (base_url or _base_url()).rstrip("/")
        self.timeout = timeout

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.get(url, params=params)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()

    # ---- Soul ---------------------------------------------------------------------
    async def get_soul_status(self, token_id: int) -> dict[str, Any] | None:
        """Public scrubbed soul info: provider, model, hasSystemPrompt, personality, etc.
        Never returns apiKey or systemPrompt text."""
        return await self._get(f"/api/soul/status/{token_id}")

    # ---- Trades -------------------------------------------------------------------
    async def get_trade_summary(self, token_id: int) -> dict[str, Any] | None:
        """Dashboard snapshot: PnL + recent trades."""
        return await self._get(f"/api/trades/{token_id}/summary")

    async def get_trade_pnl(self, token_id: int) -> dict[str, Any] | None:
        """Total + per-token PnL. Single source of truth: do NOT replicate."""
        return await self._get(f"/api/trades/{token_id}/pnl")

    async def get_trades(
        self,
        token_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
        action: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any] | None:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if action:
            params["action"] = action
        if token:
            params["token"] = token
        return await self._get(f"/api/trades/{token_id}", params=params)

    # ---- Knowledge ----------------------------------------------------------------
    async def get_knowledge_sources(self, token_id: int) -> dict[str, Any] | None:
        return await self._get(f"/api/knowledge/{token_id}")

    # ---- Leaderboard --------------------------------------------------------------
    async def get_leaderboard(
        self,
        *,
        sort: str = "intelligence",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        data = await self._get("/api/leaderboard/agents", params={"sort": sort, "limit": limit})
        if isinstance(data, dict):
            return data.get("agents") or data.get("items") or []
        if isinstance(data, list):
            return data
        return []

    async def find_in_leaderboard(self, token_id: int) -> dict[str, Any] | None:
        """Pull the leaderboard, return the row matching this tokenId. Returns None
        if the agent isn't in the snapshot: leaderboard is sticky but not exhaustive."""
        for entry in await self.get_leaderboard(limit=1000):
            entry_id = entry.get("agentId") or entry.get("tokenId") or entry.get("id")
            try:
                if int(entry_id) == token_id:
                    return entry
            except (TypeError, ValueError):
                continue
        return None

    async def get_leaderboard_stats(self) -> dict[str, Any] | None:
        return await self._get("/api/leaderboard/stats")

    # ---- Marketplace --------------------------------------------------------------
    async def list_marketplace_listings(
        self,
        *,
        sort: str = "recent",
        status: str = "created",
        limit: int = 50,
        offset: int = 0,
        lister: str | None = None,
        min_price: str | None = None,
        max_price: str | None = None,
    ) -> dict[str, Any] | None:
        """Browse the marketplace. `status` one of created|completed|cancelled|expired;
        `sort` one of recent|price_asc|price_desc (server validates, falls back to recent)."""
        params: dict[str, Any] = {"sort": sort, "status": status, "limit": limit, "offset": offset}
        if lister:
            params["lister"] = lister
        if min_price is not None:
            params["minPrice"] = min_price
        if max_price is not None:
            params["maxPrice"] = max_price
        return await self._get("/api/marketplace/listings", params=params)

    async def get_marketplace_sales_recent(self, *, limit: int = 25) -> dict[str, Any] | None:
        return await self._get("/api/marketplace/sales/recent", params={"limit": limit})

    async def get_marketplace_listings(self, token_id: int) -> dict[str, Any] | None:
        return await self._get(f"/api/marketplace/listings/{token_id}")

    async def get_marketplace_offers(self, token_id: int) -> dict[str, Any] | None:
        return await self._get(f"/api/marketplace/offers/{token_id}")

    async def get_marketplace_activity(self, token_id: int) -> dict[str, Any] | None:
        return await self._get(f"/api/marketplace/agents/{token_id}/activity")

    # ---- Triggers / quota ---------------------------------------------------------
    async def get_triggers(self, token_id: int) -> dict[str, Any] | None:
        return await self._get(f"/api/triggers/{token_id}")

    async def get_quota(self, wallet: str) -> dict[str, Any] | None:
        return await self._get(f"/api/quota/{wallet}")


@lru_cache(maxsize=1)
def client() -> BortApiClient:
    """Default-config singleton. Replace by instantiating BortApiClient(...) directly."""
    return BortApiClient()


def reset_client() -> None:
    client.cache_clear()
