"""bort_list_agent_uri: deep link into the BORT dapp for owner-signed marketplace actions.

Listing / cancelling / accepting offers / buying on MarketplaceV3 must be signed by the
NFT owner's wallet (createListing also needs a prior setApprovalForAll on BAP578). The
plugin's operator key is a vault-permission operator, not an ERC-721 operator, so it
cannot do any of this on a user's behalf. This tool returns the right dapp URL plus the
steps the owner takes there.
"""
from __future__ import annotations

import json
from typing import Any

from .. import bort_chain, bort_marketplace


_INTENTS = {
    "list":   "Create or edit a listing (seller dashboard).",
    "manage": "Manage your listings: change price or cancel (seller dashboard).",
    "offers": "View / accept offers made on your agents.",
    "view":   "Open the agent's marketplace detail page (buy or make an offer).",
}

SCHEMA = {
    "name": "bort_list_agent_uri",
    "description": (
        "Generate a BORT dapp deep link for a marketplace action on a BAP-578 agent NFT "
        "(list / manage / view / offers) plus the steps to complete it. The dapp requires "
        "the owner's own wallet to sign: this plugin's operator key cannot list, cancel, "
        "buy, or accept offers on a user's behalf. Optionally verifies the on-chain owner."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "token_id": {"type": "integer", "description": "BAP-578 NFT token ID (e.g. 11100)."},
            "intent": {
                "type": "string",
                "enum": ["list", "manage", "offers", "view"],
                "default": "list",
                "description": "What the owner wants to do in the dapp.",
            },
            "verify_owner": {
                "type": "boolean",
                "default": True,
                "description": "If true, also read BAP578.ownerOf(token_id) and include it.",
            },
        },
        "required": ["token_id"],
    },
}


async def handle(args: dict[str, Any], **kwargs) -> str:
    token_id = int(args["token_id"])
    intent = args.get("intent", "list")
    if intent not in _INTENTS:
        return json.dumps({"error": f"unknown intent {intent!r}; one of {sorted(_INTENTS)}"})

    if intent in ("list", "manage"):
        url = bort_marketplace.my_listings_url()
        steps = [
            "Open the link and connect the wallet that owns this agent.",
            "If you have never listed this agent before, approve the marketplace for BAP578 (setApprovalForAll): the dapp prompts this once.",
            "Choose 'Create listing', set the price in BNB, and confirm. To edit or cancel an existing listing, use the controls on its row.",
        ]
    elif intent == "offers":
        url = bort_marketplace.my_offers_url()
        steps = [
            "Open the link and connect the wallet that owns this agent.",
            "Review incoming offers (paid in WBNB). Accepting transfers the NFT and unwraps WBNB to BNB for you.",
        ]
    else:  # view
        url = bort_marketplace.listing_url(token_id)
        steps = [
            "Open the link to the agent's detail page.",
            "If it is listed, 'Buy now' settles in native BNB. Otherwise you can make an offer in WBNB.",
        ]

    payload: dict[str, Any] = {
        "token_id": token_id,
        "intent": intent,
        "intent_description": _INTENTS[intent],
        "url": url,
        "detail_url": bort_marketplace.listing_url(token_id),
        "marketplace_contract": bort_marketplace.MARKETPLACE_V3,
        "wbnb": bort_marketplace.WBNB,
        "platform_fee_bps": bort_marketplace.PLATFORM_FEE_BPS_DEFAULT,
        "requires_owner_wallet": True,
        "operator_key_can_do_this": False,
        "note": "The plugin operator key cannot list/cancel/buy/accept on the marketplace: those need the owner's wallet signature in the dapp.",
        "steps": steps,
    }

    if args.get("verify_owner", True):
        try:
            payload["onchain_owner"] = await bort_chain.get_owner(token_id)
        except Exception as e:  # noqa: BLE001
            payload["onchain_owner"] = None
            payload["owner_lookup_error"] = f"{type(e).__name__}: {e}"

    return json.dumps(payload, ensure_ascii=False)


def register_list_agent_uri(ctx) -> None:
    ctx.register_tool(
        name="bort_list_agent_uri",
        toolset="bort",
        schema=SCHEMA,
        handler=handle,
        is_async=True,
        description="Deep link + steps for owner-signed BORT marketplace actions.",
        emoji="🔗",
    )
