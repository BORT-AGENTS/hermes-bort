"""Unit tests for the action payload codec. Pure encoding, no chain access."""
from __future__ import annotations

import pytest

from hermes_bort.action_codec import (
    ACTION_SCHEMAS,
    ActionCodecError,
    encode_payload,
    get_action_schema,
    list_actions_for_logic,
    supports,
)


def test_unknown_action_raises():
    with pytest.raises(ActionCodecError):
        encode_payload("not_a_real_action", {})


def test_buy_token_encodes_with_defaults():
    payload = encode_payload("buy_token", {
        "token_address": "0x2A846AAaf896EF393cCb76398c1d96eA97374444",
        "amount_bnb_wei": 1_000_000_000_000_000,  # 0.001 BNB
        # slippage_bps not provided: should default to 300
    })
    # ABI-encoded (address, uint256, uint256) = 3 × 32 bytes = 96
    assert len(payload) == 96


def test_buy_token_missing_required_param_raises():
    with pytest.raises(ActionCodecError, match="amount_bnb_wei"):
        encode_payload("buy_token", {
            "token_address": "0x2A846AAaf896EF393cCb76398c1d96eA97374444",
        })


def test_open_position_full_args():
    payload = encode_payload("open_position", {
        "token_address":    "0x2A846AAaf896EF393cCb76398c1d96eA97374444",
        "amount_bnb_wei":   10_000_000_000_000_000,  # 0.01 BNB
        "slippage_bps":     500,
        "stop_loss_bps":    3000,
        "take_profit_bps":  15000,
    })
    # 5 × 32 bytes
    assert len(payload) == 160


def test_open_position_uses_defaults_for_sl_tp():
    payload_with_defaults = encode_payload("open_position", {
        "token_address":  "0x2A846AAaf896EF393cCb76398c1d96eA97374444",
        "amount_bnb_wei": 10_000_000_000_000_000,
    })
    payload_explicit = encode_payload("open_position", {
        "token_address":   "0x2A846AAaf896EF393cCb76398c1d96eA97374444",
        "amount_bnb_wei":  10_000_000_000_000_000,
        "slippage_bps":    300,
        "stop_loss_bps":   5000,
        "take_profit_bps": 20000,
    })
    assert payload_with_defaults == payload_explicit


def test_record_learning_accepts_hex_data_hash():
    payload = encode_payload("record_learning", {
        "data_hash": "0x" + "11" * 32,
        # interaction_count uses default 1
    })
    assert len(payload) == 64  # bytes32 + uint256


def test_record_learning_accepts_data_hash_without_prefix():
    payload = encode_payload("record_learning", {
        "data_hash": "11" * 32,
        "interaction_count": 1,
    })
    assert len(payload) == 64


def test_record_learning_rejects_short_hash():
    with pytest.raises(ActionCodecError, match="bytes32"):
        encode_payload("record_learning", {"data_hash": "0x1234"})


def test_get_price_bool_coercion():
    # Test that string "true"/"false" coerces to bool
    p_true_str = encode_payload("get_price", {
        "token_address": "0x2A846AAaf896EF393cCb76398c1d96eA97374444",
        "amount_in": 1000,
        "is_buy_quote": "true",
    })
    p_true_bool = encode_payload("get_price", {
        "token_address": "0x2A846AAaf896EF393cCb76398c1d96eA97374444",
        "amount_in": 1000,
        "is_buy_quote": True,
    })
    assert p_true_str == p_true_bool


def test_address_checksum_normalization():
    # Lowercase vs checksummed should produce the same payload
    p1 = encode_payload("check_balance", {"token_address": "0x2a846aaaf896ef393ccb76398c1d96ea97374444"})
    p2 = encode_payload("check_balance", {"token_address": "0x2A846AAaf896EF393cCb76398c1d96eA97374444"})
    assert p1 == p2


def test_supports_hunter_specific_action():
    assert supports("open_position", "Hunter") is True
    assert supports("open_position", "Trading V5") is False
    assert supports("open_position", "CTO") is False


def test_supports_shared_action():
    for logic in ("Hunter", "Trading V5", "CTO"):
        assert supports("buy_token", logic) is True


def test_supports_unknown_action_or_logic():
    assert supports("fake_action", "Hunter") is False
    assert supports("buy_token", "Unknown Logic") is False
    assert supports("buy_token", None) is False


def test_list_actions_for_hunter_contains_open_position():
    actions = list_actions_for_logic("Hunter")
    names = {a["name"] for a in actions}
    assert "open_position" in names
    assert "buy_token" in names
    # Each entry has the expected keys
    for a in actions:
        assert "params" in a
        assert isinstance(a["params"], list)


def test_list_actions_for_trading_v5_excludes_position_management():
    actions = list_actions_for_logic("Trading V5")
    names = {a["name"] for a in actions}
    assert "open_position" not in names
    assert "close_position" not in names
    # Common trading actions still present
    assert "buy_token" in names


def test_get_action_schema_returns_dict():
    schema = get_action_schema("buy_token")
    assert schema["category"] == "trading"
    assert "Hunter" in schema["supported_logics"]
    assert any(p["name"] == "amount_bnb_wei" for p in schema["params"])


def test_get_action_schema_unknown_raises():
    with pytest.raises(ActionCodecError):
        get_action_schema("nonexistent_action_xyz")
