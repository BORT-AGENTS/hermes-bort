"""Marketplace helpers for the BORT dapp.

The BORT marketplace (MarketplaceV3, a Thirdweb V3 router on BSC mainnet) settles
listings in native BNB and offers in WBNB. Creating / cancelling listings, accepting
offers, and buying require the NFT owner (or an approved operator of *their* wallet)
to sign: the plugin's BORT_OPERATOR_PRIVATE_KEY is a vault-permission operator, not
an ERC-721 operator, so it cannot transact on the marketplace on a user's behalf.

So write-side marketplace flows are surfaced as deep links into the dapp, where the
user signs with their own wallet. Read-side (browse / inspect) goes through the
runtime API and needs no key.
"""
from __future__ import annotations

import os

# MarketplaceV3 router on BSC mainnet (ASSET_ROLE locked to BAP578 only).
MARKETPLACE_V3 = "0x73B35e03bBC4F0F59B47106F70Cd90f579D3497b"
WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
PLATFORM_FEE_BPS_DEFAULT = 250  # 2.5% to the BORT dev wallet

DEFAULT_DAPP_URL = "https://www.bortagent.xyz"


def dapp_base_url() -> str:
    """Base URL of the BORT dapp (hash-routed SPA). Override with BORT_DAPP_URL."""
    return os.environ.get("BORT_DAPP_URL", DEFAULT_DAPP_URL).rstrip("/")


def marketplace_url() -> str:
    return f"{dapp_base_url()}/#/marketplace"


def listing_url(token_id: int) -> str:
    """Detail view for an agent: resolves tokenId to its active listing if any."""
    return f"{dapp_base_url()}/#/marketplace/{int(token_id)}"


def seller_url(address: str) -> str:
    return f"{dapp_base_url()}/#/marketplace?seller={address}"


def my_listings_url() -> str:
    """Seller dashboard: create a new listing, edit price, or cancel."""
    return f"{dapp_base_url()}/#/my-listings"


def my_offers_url() -> str:
    return f"{dapp_base_url()}/#/my-offers"
