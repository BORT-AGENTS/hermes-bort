"""Policy load + decision tests. Pure-unit, no chain access."""
from __future__ import annotations

import pytest

from hermes_bort import bort_policy


def test_default_policy_loads_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("BORT_POLICY_PATH", str(tmp_path / "does-not-exist.yaml"))
    policy = bort_policy.BortPolicy.load()
    assert policy.mode == "enforce"
    assert policy.per_action["record_learning"] == "auto"
    assert policy.per_action["buy_token"] == "confirm"


def test_record_learning_is_auto_by_default():
    policy = bort_policy.BortPolicy(bort_policy.DEFAULT_POLICY)
    decision = policy.decide("record_learning")
    assert decision.disposition == "auto"


def test_buy_token_is_confirm_by_default():
    policy = bort_policy.BortPolicy(bort_policy.DEFAULT_POLICY)
    decision = policy.decide("buy_token", value_bnb=0.01)
    assert decision.disposition == "confirm"


def test_buy_token_over_cap_is_blocked():
    policy = bort_policy.BortPolicy(bort_policy.DEFAULT_POLICY)
    # Cap is 0.1 BNB; 0.5 should block
    decision = policy.decide("buy_token", value_bnb=0.5)
    assert decision.disposition == "block"
    assert "exceeds per-action cap" in decision.reason


def test_unknown_action_defaults_to_confirm():
    policy = bort_policy.BortPolicy({"mode": "enforce", "per_action": {}})
    decision = policy.decide("unknown_future_action")
    assert decision.disposition == "confirm"


def test_off_mode_auto_approves_everything():
    policy = bort_policy.BortPolicy({"mode": "off"})
    for action in ("buy_token", "drain_vault", "anything"):
        assert policy.decide(action, value_bnb=999).disposition == "auto"


def test_write_default_policy_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "bort-policy.yaml"
    monkeypatch.setenv("BORT_POLICY_PATH", str(path))
    written = bort_policy.write_default_policy()
    assert written.exists()
    # Reload and verify
    policy = bort_policy.BortPolicy.load()
    assert policy.mode == "enforce"
    assert policy.per_action_max_bnb["buy_token"] == 0.1
