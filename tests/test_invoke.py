"""bort_invoke dry-run integration tests against BSC mainnet.

No real broadcasts: BORT_ALLOW_BROADCAST is intentionally not set. Exercises
the full pre-flight chain, payload encoding, and refusal modes against live chain.
"""
from __future__ import annotations

import json
import os

import pytest

from hermes_bort.tools.invoke import handle as invoke, SEL_FORWARD_HANDLE_ACTION


TEST_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_TOKEN_ID = int(os.environ.get("BORT_TEST_TOKEN_ID", "11100"))


@pytest.mark.asyncio
async def test_invoke_unknown_action_returns_clear_error():
    raw = await invoke({"token_id": TEST_TOKEN_ID, "action": "totally_made_up", "args": {}})
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "unknown action" in parsed["error"]


@pytest.mark.asyncio
async def test_invoke_action_not_supported_by_logic_returns_clear_error(monkeypatch):
    # open_position is Hunter-only. Agent 11100 is a Hunter, so this should NOT raise.
    # But if we use buy_token, supported, vs an obviously-unsupported pretend action it would.
    # Test for unsupported scenario: when the test agent is Hunter, configure_campaign isn't.
    monkeypatch.setenv("BORT_OPERATOR_PRIVATE_KEY", TEST_KEY)
    raw = await invoke({
        "token_id": TEST_TOKEN_ID,
        "action":   "configure_campaign",  # would be CTO if it were in our codec
        "args":     {},
    })
    parsed = json.loads(raw)
    # configure_campaign isn't in our codec yet, so it returns unknown-action error.
    # When we add CTO actions, this test will need updating.
    assert "error" in parsed


@pytest.mark.asyncio
async def test_invoke_missing_required_arg_returns_clear_error(monkeypatch):
    monkeypatch.setenv("BORT_OPERATOR_PRIVATE_KEY", TEST_KEY)
    raw = await invoke({
        "token_id": TEST_TOKEN_ID,
        "action":   "buy_token",
        "args":     {"token_address": "0x2A846AAaf896EF393cCb76398c1d96eA97374444"},
        # missing amount_bnb_wei
    })
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "amount_bnb_wei" in parsed["error"]


@pytest.mark.asyncio
async def test_invoke_blocks_without_permission(monkeypatch):
    """With a random operator key (no permission granted), invoke should refuse.

    record_learning is the action: it's `auto` in default policy so we get past
    policy and the real block is on can_forward.
    """
    monkeypatch.delenv("BORT_ALLOW_BROADCAST", raising=False)
    monkeypatch.setenv("BORT_OPERATOR_PRIVATE_KEY", TEST_KEY)

    raw = await invoke({
        "token_id": TEST_TOKEN_ID,
        "action":   "record_learning",
        "args":     {"data_hash": "0x" + "ab" * 32, "interaction_count": 1},
    })
    parsed = json.loads(raw)

    # Calldata should target VPMv2.forwardHandleAction
    assert parsed["calldata"].startswith("0x")
    assert parsed["calldata"][2:10].lower() == SEL_FORWARD_HANDLE_ACTION[2:].lower()
    assert parsed["vpm_proxy"].lower() == "0x0fa3f984f7999d31c28055260637d1bcea34919a"

    pf = parsed["preflight"]
    # Test operator (Anvil key) was never granted permission on agent 11100 → blocked
    assert pf.get("can_forward") is False
    assert parsed["status"] == "blocked"
    assert "WRITE permission" in parsed["reason"]


@pytest.mark.asyncio
async def test_invoke_buy_token_blocks_by_policy_confirm(monkeypatch):
    """buy_token defaults to `confirm` in the policy, so even with permission it
    should refuse without explicit confirmation. We don't bother granting
    permission for this test: the policy refusal takes priority over can_forward
    only if can_forward passes. Either way the call must not broadcast."""
    monkeypatch.delenv("BORT_ALLOW_BROADCAST", raising=False)
    monkeypatch.setenv("BORT_OPERATOR_PRIVATE_KEY", TEST_KEY)

    raw = await invoke({
        "token_id": TEST_TOKEN_ID,
        "action":   "buy_token",
        "args": {
            "token_address":  "0x2A846AAaf896EF393cCb76398c1d96eA97374444",  # BORT
            "amount_bnb_wei": 1_000_000_000_000_000,  # 0.001 BNB
        },
    })
    parsed = json.loads(raw)
    assert parsed["status"] in {"blocked", "needs_confirm"}


@pytest.mark.asyncio
async def test_invoke_buy_token_exceeds_per_action_cap(monkeypatch):
    """policy has per_action_max_bnb.buy_token = 0.1. 1 BNB should hit the cap."""
    monkeypatch.delenv("BORT_ALLOW_BROADCAST", raising=False)
    monkeypatch.setenv("BORT_OPERATOR_PRIVATE_KEY", TEST_KEY)

    raw = await invoke({
        "token_id":  TEST_TOKEN_ID,
        "action":    "buy_token",
        "args": {
            "token_address":  "0x2A846AAaf896EF393cCb76398c1d96eA97374444",
            "amount_bnb_wei": 1_000_000_000_000_000_000,  # 1 BNB
        },
        "value_bnb": 1.0,
    })
    parsed = json.loads(raw)
    # Either blocked due to cap (if can_forward passes somehow) or blocked due to permission.
    # Both refuse to broadcast: that's what we care about.
    assert parsed["status"] in {"blocked", "needs_confirm"}


# ---------------------------------------------------------------------------
# confirm-tier approval gate (Phase 1.5b: B)
# ---------------------------------------------------------------------------
async def _fake_can_forward_true(token_id, vault_id, accessor):  # noqa: ARG001
    return True


_BUY_ARGS = {
    "token_address":  "0x2A846AAaf896EF393cCb76398c1d96eA97374444",
    "amount_bnb_wei": 1_000_000_000_000_000,  # 0.001 BNB: under the 0.1 cap
}


@pytest.mark.asyncio
async def test_invoke_confirm_action_approved_promotes(monkeypatch):
    """can_forward passes + user approves the confirm prompt → promoted to broadcast
    path (BORT_ALLOW_BROADCAST not set → simulated_only)."""
    monkeypatch.setattr("hermes_bort.bort_chain.can_forward", _fake_can_forward_true)
    monkeypatch.setattr("hermes_bort.approval.request_action_approval", lambda a, d: "approved")
    monkeypatch.setenv("BORT_OPERATOR_PRIVATE_KEY", TEST_KEY)
    monkeypatch.delenv("BORT_ALLOW_BROADCAST", raising=False)

    raw = await invoke({"token_id": TEST_TOKEN_ID, "action": "buy_token", "args": dict(_BUY_ARGS)})
    parsed = json.loads(raw)
    assert parsed.get("approval") == "approved"
    assert parsed["status"] == "simulated_only"


@pytest.mark.asyncio
async def test_invoke_confirm_action_denied_blocks(monkeypatch):
    monkeypatch.setattr("hermes_bort.bort_chain.can_forward", _fake_can_forward_true)
    monkeypatch.setattr("hermes_bort.approval.request_action_approval", lambda a, d: "denied")
    monkeypatch.setenv("BORT_OPERATOR_PRIVATE_KEY", TEST_KEY)

    raw = await invoke({"token_id": TEST_TOKEN_ID, "action": "buy_token", "args": dict(_BUY_ARGS)})
    parsed = json.loads(raw)
    assert parsed["status"] == "blocked"
    assert "denied" in parsed["reason"].lower()


@pytest.mark.asyncio
async def test_invoke_confirm_action_gateway_pending(monkeypatch):
    monkeypatch.setattr("hermes_bort.bort_chain.can_forward", _fake_can_forward_true)
    monkeypatch.setattr("hermes_bort.approval.request_action_approval", lambda a, d: "gateway_pending")
    monkeypatch.setenv("BORT_OPERATOR_PRIVATE_KEY", TEST_KEY)

    raw = await invoke({"token_id": TEST_TOKEN_ID, "action": "buy_token", "args": dict(_BUY_ARGS)})
    parsed = json.loads(raw)
    assert parsed["status"] == "approval_required"


@pytest.mark.asyncio
async def test_invoke_confirm_action_noninteractive_keeps_needs_confirm(monkeypatch):
    """No mock on request_action_approval: in the test env it returns noninteractive."""
    monkeypatch.setattr("hermes_bort.bort_chain.can_forward", _fake_can_forward_true)
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)
    monkeypatch.delenv("HERMES_YOLO_MODE", raising=False)
    monkeypatch.setenv("BORT_OPERATOR_PRIVATE_KEY", TEST_KEY)

    raw = await invoke({"token_id": TEST_TOKEN_ID, "action": "buy_token", "args": dict(_BUY_ARGS)})
    parsed = json.loads(raw)
    assert parsed["status"] == "needs_confirm"
    assert parsed.get("approval") == "noninteractive"


@pytest.mark.asyncio
async def test_invoke_cap_exceeded_blocks_before_confirm_gate(monkeypatch):
    """amount_bnb_wei over the 0.1 cap → policy disposition 'block', confirm gate never fires."""
    monkeypatch.setattr("hermes_bort.bort_chain.can_forward", _fake_can_forward_true)
    monkeypatch.setenv("BORT_OPERATOR_PRIVATE_KEY", TEST_KEY)

    raw = await invoke({
        "token_id": TEST_TOKEN_ID, "action": "buy_token",
        "args": {"token_address": _BUY_ARGS["token_address"],
                 "amount_bnb_wei": 1_000_000_000_000_000_000},  # 1 BNB > 0.1 cap
    })
    parsed = json.loads(raw)
    assert parsed["status"] == "blocked"
    assert "cap" in parsed["reason"].lower() or "exceeds" in parsed["reason"].lower()
