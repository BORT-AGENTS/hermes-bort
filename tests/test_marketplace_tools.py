"""Tests for the Phase 2 marketplace tools and bort_marketplace helpers.

Read tools are exercised against a fake BortApiClient (no network). The deep-link tool
runs offline with verify_owner=False; with verify_owner=True it does a real chain read
(covered loosely: owner just has to be a 0x address or null on failure).
"""
from __future__ import annotations

import json
import os

import pytest

from hermes_bort import bort_api, bort_marketplace
from hermes_bort.tools.marketplace_browse import handle as browse
from hermes_bort.tools.marketplace_agent import handle as agent
from hermes_bort.tools.list_agent_uri import handle as list_uri


TEST_TOKEN_ID = int(os.environ.get("BORT_TEST_TOKEN_ID", "11100"))


# ----- bort_marketplace helpers -----
def test_dapp_urls_default_and_override(monkeypatch):
    monkeypatch.delenv("BORT_DAPP_URL", raising=False)
    assert bort_marketplace.dapp_base_url() == "https://www.bortagent.xyz"
    assert bort_marketplace.listing_url(11100) == "https://www.bortagent.xyz/#/marketplace/11100"
    assert bort_marketplace.my_listings_url().endswith("/#/my-listings")
    assert bort_marketplace.seller_url("0xabc").endswith("?seller=0xabc")

    monkeypatch.setenv("BORT_DAPP_URL", "https://staging.example.com/")
    assert bort_marketplace.dapp_base_url() == "https://staging.example.com"
    assert bort_marketplace.marketplace_url() == "https://staging.example.com/#/marketplace"


def test_marketplace_constants():
    assert bort_marketplace.MARKETPLACE_V3.lower() == "0x73b35e03bbc4f0f59b47106f70cd90f579d3497b"
    assert bort_marketplace.WBNB.lower() == "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
    assert bort_marketplace.PLATFORM_FEE_BPS_DEFAULT == 250


# ----- fake API client -----
class _FakeApi:
    async def list_marketplace_listings(self, **kw):
        return {"total": 2, "items": [{"token_id": "11100", "price_per_token": "1000000000000000000",
                                       "lister": kw.get("lister") or "0xseller", "listing_id": "1"}],
                "sort": kw.get("sort", "recent")}

    async def get_marketplace_sales_recent(self, **kw):
        return {"items": [{"token_id": "42", "total_price": "2000000000000000000"}]}

    async def get_marketplace_listings(self, token_id):
        return {"tokenId": str(token_id), "items": [{"status": "created", "listing_id": "1"}],
                "active": [{"status": "created", "listing_id": "1", "price_per_token": "1000000000000000000"}]}

    async def get_marketplace_offers(self, token_id):
        return {"tokenId": str(token_id), "items": [{"offer_id": "9", "total_price": "5e17"}]}

    async def get_marketplace_activity(self, token_id):
        return {"tokenId": str(token_id), "items": [{"event": "NewListing"}, {"event": "NewSale"}]}


@pytest.fixture
def fake_api(monkeypatch):
    fake = _FakeApi()
    monkeypatch.setattr(bort_api, "client", lambda: fake)
    return fake


# ----- bort_marketplace_browse -----
@pytest.mark.asyncio
async def test_browse_returns_listings_and_sales(fake_api):
    raw = await browse({"sort": "price_asc", "limit": 10})
    parsed = json.loads(raw)
    assert parsed["sort"] == "price_asc"
    assert parsed["total"] == 2
    assert isinstance(parsed["listings"], list) and parsed["listings"][0]["token_id"] == "11100"
    assert isinstance(parsed["recent_sales"], list)
    assert parsed["marketplace_contract"] == bort_marketplace.MARKETPLACE_V3
    assert parsed["browse_url"].endswith("/#/marketplace")


@pytest.mark.asyncio
async def test_browse_with_seller_filter_and_no_sales(fake_api):
    raw = await browse({"seller": "0xSELLER", "include_recent_sales": False})
    parsed = json.loads(raw)
    assert "recent_sales" not in parsed
    assert parsed["listings"][0]["lister"] == "0xSELLER"
    assert "seller=0xSELLER" in parsed["browse_url"]


# ----- bort_marketplace_agent -----
@pytest.mark.asyncio
async def test_agent_merges_listings_offers_activity(fake_api):
    raw = await agent({"token_id": TEST_TOKEN_ID})
    parsed = json.loads(raw)
    assert parsed["token_id"] == TEST_TOKEN_ID
    assert parsed["is_listed"] is True
    assert parsed["active_listings"][0]["listing_id"] == "1"
    assert parsed["offers"][0]["offer_id"] == "9"
    assert len(parsed["activity"]) == 2
    assert parsed["detail_url"].endswith(f"/#/marketplace/{TEST_TOKEN_ID}")


@pytest.mark.asyncio
async def test_agent_can_skip_offers_and_activity(fake_api):
    raw = await agent({"token_id": TEST_TOKEN_ID, "include_offers": False, "include_activity": False})
    parsed = json.loads(raw)
    assert "offers" not in parsed
    assert "activity" not in parsed
    assert parsed["is_listed"] is True


# ----- bort_list_agent_uri -----
@pytest.mark.asyncio
async def test_list_agent_uri_list_intent_offline():
    raw = await list_uri({"token_id": 11100, "intent": "list", "verify_owner": False})
    parsed = json.loads(raw)
    assert parsed["intent"] == "list"
    assert parsed["url"].endswith("/#/my-listings")
    assert parsed["operator_key_can_do_this"] is False
    assert parsed["requires_owner_wallet"] is True
    assert any("setApprovalForAll" in s for s in parsed["steps"])
    assert "onchain_owner" not in parsed


@pytest.mark.asyncio
async def test_list_agent_uri_view_intent_offline():
    raw = await list_uri({"token_id": 6981, "intent": "view", "verify_owner": False})
    parsed = json.loads(raw)
    assert parsed["url"].endswith("/#/marketplace/6981")
    assert parsed["intent_description"]


@pytest.mark.asyncio
async def test_list_agent_uri_bad_intent():
    raw = await list_uri({"token_id": 11100, "intent": "bogus", "verify_owner": False})
    parsed = json.loads(raw)
    assert "error" in parsed


@pytest.mark.asyncio
async def test_list_agent_uri_verify_owner_live():
    """With verify_owner=True it does a real ownerOf read; tolerate RPC hiccups."""
    raw = await list_uri({"token_id": TEST_TOKEN_ID, "intent": "manage"})
    parsed = json.loads(raw)
    assert parsed["url"].endswith("/#/my-listings")
    owner = parsed.get("onchain_owner")
    assert owner is None or (isinstance(owner, str) and owner.startswith("0x") and len(owner) == 42)
