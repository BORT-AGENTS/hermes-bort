"""Per-logic-contract adapters.

Hunter, Trading V5, and CTO logic contracts share the universal
`handleAction(uint256, string, bytes)` write entry, plus `agentBNBBalance` /
`agentTokenBalance` read accessors. But their stateful read surfaces diverge:

- Hunter:   `Position[]` per token, multi-token tracking
- Trading:  no per-token struct, just balances + metrics
- CTO:      `Campaign` + `TakeProfitTranche[]` + `CampaignThresholds`

Each adapter exposes an async `read(token_id)` returning a JSON-serializable dict.
The registry maps a lowercased logic address to the matching adapter class.

Earlier guesses for the Hunter Position struct (7 fields) were wrong. The verified
order is below: 8 fields, source-confirmed at contracts/logic/HunterAgentLogic.sol.
"""
from __future__ import annotations

from typing import Any

from web3 import AsyncWeb3
from web3.contract.async_contract import AsyncContract

from .bort_chain import ADDRESSES, web3


# ---- Position struct (Hunter): 8 fields, source-verified ------------------------
_POSITION_TUPLE = [
    {"name": "tokenAddress",       "type": "address"},
    {"name": "entryAmountBnb",     "type": "uint256"},
    {"name": "tokenAmount",        "type": "uint256"},
    {"name": "entryTimestamp",     "type": "uint256"},
    {"name": "stopLossBps",        "type": "uint256"},
    {"name": "takeProfitBps",      "type": "uint256"},
    {"name": "takeProfitExecuted", "type": "bool"},
    {"name": "active",             "type": "bool"},
]

# ---- Campaign struct (CTO): 11 fields -------------------------------------------
_CAMPAIGN_TUPLE = [
    {"name": "tokenAddress",         "type": "address"},
    {"name": "status",               "type": "uint8"},
    {"name": "entryMarketCapWei",    "type": "uint256"},
    {"name": "entryTokenAmount",     "type": "uint256"},
    {"name": "remainingTokenAmount", "type": "uint256"},
    {"name": "totalBnbSpent",        "type": "uint256"},
    {"name": "totalBnbReceived",     "type": "uint256"},
    {"name": "startedAt",            "type": "uint256"},
    {"name": "completedAt",          "type": "uint256"},
    {"name": "trancheCount",         "type": "uint256"},
    {"name": "isFourMemeBonding",    "type": "bool"},
]

_TRANCHE_TUPLE = [
    {"name": "mcapMultiplierBps", "type": "uint256"},
    {"name": "sellPercentBps",    "type": "uint256"},
    {"name": "executed",          "type": "bool"},
    {"name": "executedAt",        "type": "uint256"},
    {"name": "bnbReceived",       "type": "uint256"},
]

_THRESHOLDS_TUPLE = [
    {"name": "maxTopHolderPct", "type": "uint256"},
    {"name": "minMarketCapWei", "type": "uint256"},
    {"name": "maxMarketCapWei", "type": "uint256"},
    {"name": "maxBuyAmountBnb", "type": "uint256"},
]

_METRICS_OUTPUTS = [
    {"name": "totalActions",      "type": "uint256"},
    {"name": "successfulActions", "type": "uint256"},
    {"name": "totalTrades",       "type": "uint256"},
    {"name": "lifetimePnL",       "type": "int256"},
    {"name": "totalInteractions", "type": "uint256"},
    {"name": "lastActive",        "type": "uint256"},
    {"name": "activePositions",   "type": "uint256"},
]

# Shared across all three logics
_SHARED_LOGIC_ABI: list[dict[str, Any]] = [
    {"inputs": [{"name": "tokenId", "type": "uint256"}], "name": "agentBNBBalance",
     "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [
        {"name": "tokenId", "type": "uint256"},
        {"name": "token",   "type": "address"},
     ], "name": "agentTokenBalance",
     "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "tokenId", "type": "uint256"}], "name": "getMetrics",
     "outputs": _METRICS_OUTPUTS, "stateMutability": "view", "type": "function"},
]

HUNTER_ABI: list[dict[str, Any]] = _SHARED_LOGIC_ABI + [
    {"inputs": [{"name": "tokenId", "type": "uint256"}], "name": "getActivePositions",
     "outputs": [{"type": "address[]"}], "stateMutability": "view", "type": "function"},
    {"inputs": [
        {"name": "tokenId", "type": "uint256"},
        {"name": "token",   "type": "address"},
     ], "name": "getPosition",
     "outputs": [{"components": _POSITION_TUPLE, "type": "tuple"}],
     "stateMutability": "view", "type": "function"},
]

CTO_ABI: list[dict[str, Any]] = _SHARED_LOGIC_ABI + [
    {"inputs": [{"name": "tokenId", "type": "uint256"}], "name": "getCampaign",
     "outputs": [{"components": _CAMPAIGN_TUPLE, "type": "tuple"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "tokenId", "type": "uint256"}], "name": "getAllTranches",
     "outputs": [{"components": _TRANCHE_TUPLE, "type": "tuple[]"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "tokenId", "type": "uint256"}], "name": "getThresholds",
     "outputs": [{"components": _THRESHOLDS_TUPLE, "type": "tuple"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "tokenId", "type": "uint256"}], "name": "calculatePnL",
     "outputs": [
         {"name": "realizedPnl",     "type": "int256"},
         {"name": "remainingTokens", "type": "uint256"},
     ], "stateMutability": "view", "type": "function"},
]

ERC20_ABI: list[dict[str, Any]] = [
    {"inputs": [], "name": "symbol",
     "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "decimals",
     "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "name",
     "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
]


def _wei_to_bnb(value: int) -> float:
    if value >= 0:
        return float(AsyncWeb3.from_wei(int(value), "ether"))
    return -float(AsyncWeb3.from_wei(-int(value), "ether"))


def _decode_metrics(raw: tuple) -> dict[str, Any]:
    total_actions, successful, total_trades, pnl, interactions, last_active, active_positions = raw
    return {
        "total_actions":      int(total_actions),
        "successful_actions": int(successful),
        "total_trades":       int(total_trades),
        "lifetime_pnl_wei":   int(pnl),
        "lifetime_pnl_bnb":   _wei_to_bnb(int(pnl)),
        "total_interactions": int(interactions),
        "last_active":        int(last_active),
        "active_positions":   int(active_positions),
    }


class _LogicAdapter:
    """Base class. Subclasses set `name` and `abi`."""
    name: str = "Generic"
    abi: list[dict[str, Any]] = _SHARED_LOGIC_ABI

    def __init__(self, address: str):
        self.address = AsyncWeb3.to_checksum_address(address)

    def _contract(self) -> AsyncContract:
        return web3().eth.contract(address=self.address, abi=self.abi)

    async def read(self, token_id: int) -> dict[str, Any]:
        c = self._contract()
        try:
            metrics_raw = await c.functions.getMetrics(token_id).call()
            metrics: dict[str, Any] = _decode_metrics(metrics_raw)
        except Exception as e:  # noqa: BLE001
            metrics = {"error": f"{type(e).__name__}: {e}"}
        try:
            vault_wei = int(await c.functions.agentBNBBalance(token_id).call())
            vault: dict[str, Any] = {
                "wei": vault_wei,
                "bnb": float(AsyncWeb3.from_wei(vault_wei, "ether")),
            }
        except Exception as e:  # noqa: BLE001
            vault = {"error": f"{type(e).__name__}: {e}"}
        return {"logic": self.name, "metrics": metrics, "vault": vault}


class HunterAdapter(_LogicAdapter):
    name = "Hunter"
    abi = HUNTER_ABI

    async def read(self, token_id: int) -> dict[str, Any]:
        base = await super().read(token_id)
        c = self._contract()
        try:
            position_tokens = await c.functions.getActivePositions(token_id).call()
        except Exception as e:  # noqa: BLE001
            position_tokens = []
            base["positions_error"] = f"{type(e).__name__}: {e}"
        positions: list[dict[str, Any]] = []
        for tok in position_tokens:
            try:
                pos_raw = await c.functions.getPosition(token_id, tok).call()
                erc20 = web3().eth.contract(
                    address=AsyncWeb3.to_checksum_address(tok), abi=ERC20_ABI,
                )
                symbol: str | None = None
                decimals: int = 18
                try:
                    symbol = await erc20.functions.symbol().call()
                except Exception:
                    pass
                try:
                    decimals = int(await erc20.functions.decimals().call())
                except Exception:
                    decimals = 18
                (token_address, entry_bnb, token_amount, entry_ts,
                 sl_bps, tp_bps, tp_done, active) = pos_raw
                bnb_value = float(AsyncWeb3.from_wei(int(entry_bnb), "ether"))
                tracked_amount = float(int(token_amount)) / (10 ** decimals)
                # agentTokenBalance is the live truth: the frontend displays this.
                # Position.tokenAmount only advances on Hunter-tracked trades; agentTokenBalance
                # also reflects external buys/sells.
                try:
                    current_balance_raw = int(await c.functions.agentTokenBalance(token_id, tok).call())
                    current_balance = float(current_balance_raw) / (10 ** decimals)
                except Exception:
                    current_balance = tracked_amount
                positions.append({
                    "token":            token_address,
                    "symbol":           symbol,
                    "decimals":         decimals,
                    "entry_amount_bnb": bnb_value,
                    "current_balance":  current_balance,    # live, matches frontend display
                    "tracked_amount":   tracked_amount,     # from Position struct
                    "entry_timestamp":  int(entry_ts),
                    "stop_loss_bps":    int(sl_bps),
                    "take_profit_bps":  int(tp_bps),
                    "take_profit_done": bool(tp_done),
                    "active":           bool(active),
                })
            except Exception as e:  # noqa: BLE001
                positions.append({"token": tok, "error": f"{type(e).__name__}: {e}"})
        base["positions"] = positions
        return base


class TradingV5Adapter(_LogicAdapter):
    name = "Trading V5"
    abi = _SHARED_LOGIC_ABI

    async def read(self, token_id: int) -> dict[str, Any]:
        base = await super().read(token_id)
        # Trading V5 has no per-token position struct. lifetimePnL is hardcoded to 0
        # in getMetrics: the real PnL truth lives in /api/trades/:id/pnl.
        if isinstance(base.get("metrics"), dict):
            base["metrics"]["pnl_authoritative"] = False
            base["metrics"]["pnl_source"] = "/api/trades/{token_id}/pnl"
        return base


class CTOAdapter(_LogicAdapter):
    name = "CTO"
    abi = CTO_ABI

    async def read(self, token_id: int) -> dict[str, Any]:
        base = await super().read(token_id)
        c = self._contract()
        try:
            camp_raw = await c.functions.getCampaign(token_id).call()
            (token_address, status, entry_mcap, entry_amt, remaining, total_spent,
             total_received, started_at, completed_at, tranche_count, is_bonding) = camp_raw
            base["campaign"] = {
                "token_address":        token_address,
                "status":               int(status),
                "entry_market_cap_wei": int(entry_mcap),
                "entry_token_amount":   int(entry_amt),
                "remaining_tokens":     int(remaining),
                "total_bnb_spent_wei":  int(total_spent),
                "total_bnb_recv_wei":   int(total_received),
                "started_at":           int(started_at),
                "completed_at":         int(completed_at),
                "tranche_count":        int(tranche_count),
                "is_fourmeme_bonding":  bool(is_bonding),
            }
        except Exception as e:  # noqa: BLE001
            base["campaign"] = {"error": f"{type(e).__name__}: {e}"}
        try:
            tranches_raw = await c.functions.getAllTranches(token_id).call()
            base["tranches"] = [
                {
                    "mcap_multiplier_bps": int(t[0]),
                    "sell_percent_bps":    int(t[1]),
                    "executed":            bool(t[2]),
                    "executed_at":         int(t[3]),
                    "bnb_received_wei":    int(t[4]),
                } for t in tranches_raw
            ]
        except Exception as e:  # noqa: BLE001
            base["tranches"] = {"error": f"{type(e).__name__}: {e}"}
        try:
            thr_raw = await c.functions.getThresholds(token_id).call()
            base["thresholds"] = {
                "max_top_holder_pct_bps": int(thr_raw[0]),
                "min_market_cap_wei":     int(thr_raw[1]),
                "max_market_cap_wei":     int(thr_raw[2]),
                "max_buy_amount_bnb_wei": int(thr_raw[3]),
            }
        except Exception as e:  # noqa: BLE001
            base["thresholds"] = {"error": f"{type(e).__name__}: {e}"}
        try:
            pnl_raw = await c.functions.calculatePnL(token_id).call()
            realized, remaining = pnl_raw
            base["calculated_pnl"] = {
                "realized_wei":     int(realized),
                "remaining_tokens": int(remaining),
            }
        except Exception as e:  # noqa: BLE001
            base["calculated_pnl"] = {"error": f"{type(e).__name__}: {e}"}
        if isinstance(base.get("metrics"), dict):
            base["metrics"]["pnl_authoritative"] = False
            base["metrics"]["pnl_source"] = "/api/trades/{token_id}/pnl + calculatePnL"
        return base


_ADAPTER_BY_LOGIC: dict[str, type[_LogicAdapter]] = {
    ADDRESSES["HunterLogic"].lower():    HunterAdapter,
    ADDRESSES["TradingLogicV5"].lower(): TradingV5Adapter,
    ADDRESSES["CTOLogic"].lower():       CTOAdapter,
}


def get_adapter(logic_address: str | None) -> _LogicAdapter | None:
    if not logic_address:
        return None
    cls = _ADAPTER_BY_LOGIC.get(logic_address.lower())
    if cls is None:
        return None
    return cls(logic_address)
