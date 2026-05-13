"""Tests for hermes_bort.approval: the confirm-tier approval gate.

In the test environment there's no HERMES_INTERACTIVE and no gateway context, so
request_action_approval returns "noninteractive" deterministically. We also test
the policy-flip helper.
"""
from __future__ import annotations

import pytest

from hermes_bort import approval


def test_request_action_approval_noninteractive_in_test_env(monkeypatch):
    # No interactive CLI, no gateway, no yolo → nowhere to ask → noninteractive
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)
    monkeypatch.delenv("HERMES_YOLO_MODE", raising=False)
    verdict = approval.request_action_approval("buy_token", "buy_token for agent 11100 (~0.001 BNB)")
    assert verdict == approval.NONINTERACTIVE


def test_request_action_approval_yolo_bypass(monkeypatch):
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)
    monkeypatch.setenv("HERMES_YOLO_MODE", "1")
    verdict = approval.request_action_approval("buy_token", "test")
    assert verdict == approval.APPROVED


def test_flip_policy_to_auto_updates_yaml(tmp_path, monkeypatch):
    import yaml
    from hermes_bort.bort_policy import write_default_policy

    policy_path = tmp_path / "bort-policy.yaml"
    monkeypatch.setenv("BORT_POLICY_PATH", str(policy_path))
    write_default_policy(str(policy_path))

    before = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    assert before["per_action"]["buy_token"] == "confirm"   # default

    approval._flip_policy_to_auto("buy_token")

    after = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    assert after["per_action"]["buy_token"] == "auto"
    # other entries untouched
    assert after["per_action"]["record_learning"] == "auto"


def test_flip_policy_to_auto_noop_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("BORT_POLICY_PATH", str(tmp_path / "does-not-exist.yaml"))
    # Should not raise
    approval._flip_policy_to_auto("buy_token")


def test_constants_distinct():
    vals = {approval.APPROVED, approval.DENIED, approval.NONINTERACTIVE, approval.GATEWAY_PENDING}
    assert len(vals) == 4
