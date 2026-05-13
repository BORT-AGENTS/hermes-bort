"""bort_list_actions: static catalog of action names per logic + universal off-chain reads.

Returns names and one-line summaries, NOT full ABI/payload schemas. The encoder
lives in bort_invoke / action_codec. Source of truth for the action names lives in
the BORT runtime's action-schemas: this catalog must be kept in sync if the runtime
adds new action names.
"""
from __future__ import annotations

import json
from typing import Any


# Per-logic on-chain actions
LOGIC_ACTIONS: dict[str, list[dict[str, str]]] = {
    "Hunter": [
        {"name": "open_position",      "category": "trading",  "description": "Buy a token and open a tracked position with stop-loss / take-profit."},
        {"name": "close_position",     "category": "trading",  "description": "Sell a tracked position and realize PnL."},
        {"name": "check_exit_signals", "category": "trading",  "description": "Evaluate stop-loss / take-profit triggers."},
        {"name": "buy_token",          "category": "trading",  "description": "Direct PancakeSwap buy from agent vault."},
        {"name": "sell_token",         "category": "trading",  "description": "Direct PancakeSwap sell."},
        {"name": "buy_fourmeme",       "category": "trading",  "description": "Buy on FourMeme bonding curve."},
        {"name": "sell_fourmeme",      "category": "trading",  "description": "Sell on FourMeme bonding curve."},
        {"name": "check_balance",      "category": "trading",  "description": "Read vault BNB + token balance."},
        {"name": "get_price",          "category": "trading",  "description": "Quote price for a swap amount."},
        {"name": "check_fourmeme",     "category": "trading",  "description": "Inspect FourMeme bonding curve status."},
        {"name": "record_activity",    "category": "learning", "description": "Record platform interaction."},
        {"name": "record_learning",    "category": "learning", "description": "Commit a learning leaf to MerkleTreeLearning (advances intelligenceScore)."},
    ],
    "Trading V5": [
        {"name": "buy_token",       "category": "trading",  "description": "PancakeSwap buy."},
        {"name": "sell_token",      "category": "trading",  "description": "PancakeSwap sell."},
        {"name": "buy_fourmeme",    "category": "trading",  "description": "FourMeme buy."},
        {"name": "sell_fourmeme",   "category": "trading",  "description": "FourMeme sell."},
        {"name": "check_balance",   "category": "trading",  "description": "Vault balance."},
        {"name": "get_price",       "category": "trading",  "description": "Quote price."},
        {"name": "check_fourmeme",  "category": "trading",  "description": "FourMeme status."},
        {"name": "record_activity", "category": "learning", "description": "Record platform interaction."},
        {"name": "record_learning", "category": "learning", "description": "Commit a learning leaf."},
    ],
    "CTO": [
        {"name": "configure_campaign",   "category": "campaign", "description": "Set thresholds + multi-tranche exit plan."},
        {"name": "evaluate_token",       "category": "campaign", "description": "Check a token against campaign thresholds."},
        {"name": "start_campaign",       "category": "campaign", "description": "Begin a campaign on an evaluated token."},
        {"name": "execute_buy",          "category": "campaign", "description": "Execute the campaign entry buy."},
        {"name": "check_exit_conditions","category": "campaign", "description": "Evaluate which tranche should fire next."},
        {"name": "execute_exit",         "category": "campaign", "description": "Execute one graduated exit tranche."},
        {"name": "end_campaign",         "category": "campaign", "description": "Close out the campaign and finalize PnL."},
        {"name": "get_campaign_status",  "category": "campaign", "description": "Read current campaign state (off-chain)."},
        {"name": "buy_token",            "category": "trading",  "description": "Outside-campaign PancakeSwap buy."},
        {"name": "sell_token",           "category": "trading",  "description": "Outside-campaign PancakeSwap sell."},
        {"name": "buy_fourmeme",         "category": "trading",  "description": "FourMeme buy."},
        {"name": "sell_fourmeme",        "category": "trading",  "description": "FourMeme sell."},
        {"name": "post_tweet",           "category": "social",   "description": "Post via runtime social connector."},
        {"name": "reply_tweet",          "category": "social",   "description": "Reply via social connector."},
        {"name": "monitor_mentions",     "category": "social",   "description": "Track keyword mentions."},
        {"name": "raid_post",            "category": "social",   "description": "Quote-tweet / reply at scale."},
        {"name": "report_holders",       "category": "social",   "description": "Snapshot top-holder distribution."},
        {"name": "record_activity",      "category": "learning", "description": "Record platform interaction."},
        {"name": "record_learning",      "category": "learning", "description": "Commit a learning leaf."},
    ],
}

# Universal off-chain reads: appended to every agent unless filter-excluded
UNIVERSAL_OFFCHAIN_READS: list[dict[str, str]] = [
    {"name": "get_token_info",       "description": "ERC-20 metadata (name/symbol/decimals/totalSupply)."},
    {"name": "read_contract",        "description": "Arbitrary contract read via ABI."},
    {"name": "is_contract",          "description": "Check whether an address has bytecode."},
    {"name": "get_transaction",      "description": "Look up tx receipt by hash."},
    {"name": "resolve_ens",          "description": "ENS name lookup."},
    {"name": "search_token",         "description": "Search BSC token registry by name/symbol."},
    {"name": "lookup_token",         "description": "Resolve token address from symbol or name."},
    {"name": "get_wallet_balance",   "description": "EOA BNB balance."},
    {"name": "get_token_balances",   "description": "Curated BSC token balances for a wallet."},
    {"name": "get_my_holdings",      "description": "Vault holdings for the agent (HunterAgentLogic)."},
    {"name": "get_swap_quote",       "description": "PancakeSwap V2/V3 swap quote."},
    {"name": "get_deposit_info",     "description": "Vault deposit guidance for the owner."},
    {"name": "read_x_timeline",      "description": "Read Twitter/X timeline."},
    {"name": "search_x",             "description": "Search Twitter/X."},
    {"name": "read_telegram_chat",   "description": "Read Telegram channel/chat history."},
    {"name": "search_telegram_chat", "description": "Search Telegram messages."},
    {"name": "analyze_social",       "description": "Aggregate social signal."},
    {"name": "read_youtube",         "description": "Fetch YouTube video transcript/metadata."},
    {"name": "search_reddit",        "description": "Reddit search."},
    {"name": "read_reddit_post",     "description": "Read a Reddit post + top comments."},
    {"name": "read_github_repo",     "description": "Inspect GitHub repo metadata."},
    {"name": "read_webpage",         "description": "Fetch a webpage and extract text."},
    {"name": "search_web",           "description": "General web search."},
    {"name": "read_x_user",          "description": "Read a Twitter/X profile."},
    {"name": "search_x_web",         "description": "Twitter/X web search variant."},
]

# Hunter excludes these per action-schemas.js excludeUniversal filter
HUNTER_EXCLUDED_UNIVERSALS = {"read_contract", "is_contract", "get_transaction", "resolve_ens"}


SCHEMA = {
    "name": "bort_list_actions",
    "description": (
        "List the action names available for a BORT agent given its logic type. "
        "Returns logic-specific actions plus the universal off-chain reads. "
        "Phase 1 returns names + one-line descriptions only: payload encoders "
        "land in Phase 1.5 with the bort_invoke tool."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "logic_name": {
                "type": "string",
                "enum": ["Hunter", "Trading V5", "CTO"],
                "description": "Logic type. Pull from bort_read_agent's `on_chain.state.logic_name`.",
            },
        },
        "required": ["logic_name"],
    },
}


async def handle(args: dict[str, Any], **kwargs) -> str:
    logic_name = str(args["logic_name"])
    logic_actions = LOGIC_ACTIONS.get(logic_name, [])

    if logic_name == "Hunter":
        universals = [u for u in UNIVERSAL_OFFCHAIN_READS if u["name"] not in HUNTER_EXCLUDED_UNIVERSALS]
    else:
        universals = list(UNIVERSAL_OFFCHAIN_READS)

    response = {
        "logic_name": logic_name,
        "on_chain":   logic_actions,
        "off_chain":  universals,
        "totals": {
            "on_chain":  len(logic_actions),
            "off_chain": len(universals),
            "all":       len(logic_actions) + len(universals),
        },
        "note": "Phase 1 read-only. bort_invoke (write) lands in Phase 1.5.",
    }
    return json.dumps(response, ensure_ascii=False)


def register_list_actions(ctx) -> None:
    ctx.register_tool(
        name="bort_list_actions",
        toolset="bort",
        schema=SCHEMA,
        handler=handle,
        is_async=True,
        description="List actions available for a BORT agent's logic type.",
        emoji="📜",
    )
