"""Per-action payload encoder for BAP-578 logic contracts.

`bort_invoke(token_id, action, args)` uses this to translate a friendly JSON
shape into the ABI-encoded bytes that `handleAction(tokenId, action, payload)`
expects on the logic contract.

Source of truth for action signatures: contracts/logic/HunterAgentLogic.sol
and the equivalents in Trading V5 / CTO. Verified against the action-schemas.js
catalog in the BORT runtime.

Adding a new action: add an entry to ACTION_SCHEMAS. No code changes needed
elsewhere: the encoder reads the types straight from the schema.
"""
from __future__ import annotations

from typing import Any

from eth_abi import encode as abi_encode
from web3 import AsyncWeb3


# Sensible defaults for parameters that almost always want the same value.
DEFAULT_SLIPPAGE_BPS = 300  # 3%


# Schema catalog. Keys are action names exactly as the contract expects them
# (`keccak256(bytes(action))` is what gets compared inside handleAction).
ACTION_SCHEMAS: dict[str, dict[str, Any]] = {
    # ----- Trading (Hunter / Trading V5 / CTO all support these) -----
    "buy_token": {
        "category": "trading",
        "supported_logics": ["Hunter", "Trading V5", "CTO"],
        "description": "Buy an ERC-20 token via PancakeSwap from the agent vault.",
        "params": [
            {"name": "token_address",  "type": "address",  "required": True,
             "description": "Address of the token to buy."},
            {"name": "amount_bnb_wei", "type": "uint256",  "required": True,
             "description": "BNB to spend, in wei (1 BNB = 10**18 wei)."},
            {"name": "slippage_bps",   "type": "uint256",  "default": DEFAULT_SLIPPAGE_BPS,
             "description": "Max slippage in basis points (300 = 3%)."},
        ],
    },
    "sell_token": {
        "category": "trading",
        "supported_logics": ["Hunter", "Trading V5", "CTO"],
        "description": "Sell an ERC-20 token from the agent vault for BNB.",
        "params": [
            {"name": "token_address", "type": "address", "required": True},
            {"name": "token_amount",  "type": "uint256", "required": True,
             "description": "Tokens to sell, in token's smallest unit."},
            {"name": "slippage_bps",  "type": "uint256", "default": DEFAULT_SLIPPAGE_BPS},
        ],
    },
    "buy_fourmeme": {
        "category": "trading",
        "supported_logics": ["Hunter", "Trading V5", "CTO"],
        "description": "Buy a token on the FourMeme bonding curve.",
        "params": [
            {"name": "token_address",  "type": "address", "required": True},
            {"name": "amount_bnb_wei", "type": "uint256", "required": True},
            {"name": "min_tokens",     "type": "uint256", "default": 0,
             "description": "Minimum tokens to receive (slippage protection)."},
        ],
    },
    "sell_fourmeme": {
        "category": "trading",
        "supported_logics": ["Hunter", "Trading V5", "CTO"],
        "description": "Sell on the FourMeme bonding curve.",
        "params": [
            {"name": "token_address", "type": "address", "required": True},
            {"name": "token_amount",  "type": "uint256", "required": True},
        ],
    },
    "check_balance": {
        "category": "trading",
        "supported_logics": ["Hunter", "Trading V5", "CTO"],
        "description": "Read vault BNB + token balance for one token. Goes through handleAction; cheap.",
        "params": [
            {"name": "token_address", "type": "address", "required": True},
        ],
    },
    "get_price": {
        "category": "trading",
        "supported_logics": ["Hunter", "Trading V5", "CTO"],
        "description": "Get a PancakeSwap quote for the given direction.",
        "params": [
            {"name": "token_address", "type": "address", "required": True},
            {"name": "amount_in",     "type": "uint256", "required": True},
            {"name": "is_buy_quote",  "type": "bool",    "required": True,
             "description": "True = how many tokens for BNB. False = how much BNB for tokens."},
        ],
    },
    "check_fourmeme": {
        "category": "trading",
        "supported_logics": ["Hunter", "Trading V5", "CTO"],
        "description": "Inspect FourMeme bonding-curve status for a token.",
        "params": [
            {"name": "token_address", "type": "address", "required": True},
        ],
    },

    # ----- Position management (Hunter only) -----
    "open_position": {
        "category": "trading",
        "supported_logics": ["Hunter"],
        "description": "Buy a token and open a tracked position with stop-loss / take-profit.",
        "params": [
            {"name": "token_address",    "type": "address", "required": True},
            {"name": "amount_bnb_wei",   "type": "uint256", "required": True},
            {"name": "slippage_bps",     "type": "uint256", "default": DEFAULT_SLIPPAGE_BPS},
            {"name": "stop_loss_bps",    "type": "uint256", "default": 5000,
             "description": "Exit if value drops this many bps (5000 = 50%). 0 = no stop-loss."},
            {"name": "take_profit_bps",  "type": "uint256", "default": 20000,
             "description": "Exit if value rises this many bps (20000 = 200%). 0 = no take-profit."},
        ],
    },
    "close_position": {
        "category": "trading",
        "supported_logics": ["Hunter"],
        "description": "Sell a tracked position and realize PnL.",
        "params": [
            {"name": "token_address", "type": "address", "required": True},
            {"name": "slippage_bps",  "type": "uint256", "default": DEFAULT_SLIPPAGE_BPS},
        ],
    },
    "check_exit_signals": {
        "category": "trading",
        "supported_logics": ["Hunter"],
        "description": "Evaluate stop-loss / take-profit triggers for a tracked position.",
        "params": [
            {"name": "token_address",        "type": "address", "required": True},
            {"name": "current_value_bnb_wei", "type": "uint256", "required": True,
             "description": "Current value of the position in BNB wei (from get_price)."},
        ],
    },

    # ----- Activity / learning -----
    "record_activity": {
        "category": "learning",
        "supported_logics": ["Hunter", "Trading V5", "CTO"],
        "description": "Record a platform interaction (increments interaction counter).",
        "params": [
            {"name": "platform", "type": "uint256", "required": True,
             "description": "Platform ID (free-form; agent-specific convention)."},
        ],
    },
    "record_learning": {
        "category": "learning",
        "supported_logics": ["Hunter", "Trading V5", "CTO"],
        "description": "Commit a learning leaf (32-byte digest + interaction count).",
        "params": [
            {"name": "data_hash",         "type": "bytes32", "required": True,
             "description": "Keccak/SHA digest of the learning content."},
            {"name": "interaction_count", "type": "uint256", "default": 1},
        ],
    },
}


class ActionCodecError(ValueError):
    """Raised when an action is unknown, args are missing, or types don't coerce."""


def _coerce(value: Any, ptype: str) -> Any:
    """Coerce a JSON-friendly value into the Python type eth_abi expects."""
    if ptype == "address":
        return AsyncWeb3.to_checksum_address(str(value))
    if ptype == "uint256" or ptype.startswith("uint"):
        return int(value)
    if ptype == "int256" or ptype.startswith("int"):
        return int(value)
    if ptype == "bool":
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if ptype == "bytes32":
        if isinstance(value, (bytes, bytearray)):
            b = bytes(value)
        else:
            s = str(value).strip()
            if s.startswith("0x"):
                s = s[2:]
            if len(s) != 64:
                raise ActionCodecError(
                    f"bytes32 expects 64 hex chars (32 bytes), got {len(s)}"
                )
            b = bytes.fromhex(s)
        if len(b) != 32:
            raise ActionCodecError(f"bytes32 must be exactly 32 bytes, got {len(b)}")
        return b
    if ptype == "string":
        return str(value)
    if ptype == "bytes":
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        s = str(value)
        if s.startswith("0x"):
            s = s[2:]
        return bytes.fromhex(s) if s else b""
    raise ActionCodecError(f"unsupported param type: {ptype}")


def list_actions_for_logic(logic_name: str | None) -> list[dict[str, Any]]:
    """Return the subset of actions supported by `logic_name` (e.g. 'Hunter')."""
    if not logic_name:
        return []
    out = []
    for name, schema in ACTION_SCHEMAS.items():
        if logic_name in schema["supported_logics"]:
            out.append({
                "name":        name,
                "category":    schema["category"],
                "description": schema.get("description", ""),
                "params":      schema["params"],
            })
    return out


def get_action_schema(action: str) -> dict[str, Any]:
    """Return the schema for `action` or raise."""
    if action not in ACTION_SCHEMAS:
        raise ActionCodecError(f"unknown action: {action!r}")
    return ACTION_SCHEMAS[action]


def encode_payload(action: str, args: dict[str, Any]) -> bytes:
    """Build the inner payload bytes for handleAction(tokenId, action, payload).

    Reads param order + types from ACTION_SCHEMAS[action]['params']. Applies
    defaults for any param marked with `default` if `args` doesn't override.
    Raises ActionCodecError on unknown action, missing required param, or
    bad type coercion.
    """
    schema = get_action_schema(action)
    types: list[str] = []
    values: list[Any] = []
    for param in schema["params"]:
        name = param["name"]
        ptype = param["type"]
        if name in args:
            raw = args[name]
        elif "default" in param:
            raw = param["default"]
        elif param.get("required"):
            raise ActionCodecError(f"missing required param {name!r} for action {action!r}")
        else:
            raise ActionCodecError(f"missing param {name!r} (no default) for action {action!r}")
        try:
            values.append(_coerce(raw, ptype))
        except ActionCodecError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ActionCodecError(
                f"failed to coerce {name!r}={raw!r} to {ptype}: {type(e).__name__}: {e}"
            )
        types.append(ptype)
    return abi_encode(types, values)


def supports(action: str, logic_name: str | None) -> bool:
    """True iff the action is in our catalog AND supported by the given logic."""
    if action not in ACTION_SCHEMAS or not logic_name:
        return False
    return logic_name in ACTION_SCHEMAS[action]["supported_logics"]
