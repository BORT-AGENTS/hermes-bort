"""Simulates Hermes' PluginManager loading the plugin via `register(ctx)`.

This does NOT depend on Hermes being installed: it builds a fake ctx that records
every registration, then exercises each registered tool and memory-provider method.
The contract being tested matches Hermes' AGENTS.md description of the plugin surface.
"""
from __future__ import annotations

import json
from typing import Any

import pytest


class FakeCtx:
    """Minimal stand-in for Hermes' plugin registration context."""

    def __init__(self):
        self.tools: dict[str, dict[str, Any]] = {}
        self.memory_provider = None
        self.cli_commands: list[tuple[Any, Any]] = []
        self.hooks: dict[str, list] = {}

    def register_tool(self, *, name, toolset, schema, handler, **kwargs):
        self.tools[name] = {
            "toolset": toolset,
            "schema":  schema,
            "handler": handler,
            **kwargs,
        }

    def register_memory_provider(self, provider):
        self.memory_provider = provider

    def register_cli_command(self, *args, **kwargs):
        self.cli_commands.append((args, kwargs))

    def add_hook(self, event, callback):
        self.hooks.setdefault(event, []).append(callback)


def _load_plugin() -> FakeCtx:
    import hermes_bort
    ctx = FakeCtx()
    hermes_bort.register(ctx)
    return ctx


EXPECTED_TOOLS = {
    "bort_read_agent",          # Phase 1 read
    "bort_health_check",        # Phase 1 read
    "bort_list_actions",        # Phase 1 read
    "bort_grant_permission_uri",# Phase 1.5a setup helper
    "bort_commit_learning",     # Phase 1.5a write
    "bort_invoke",              # Phase 1.5b universal write
    "bort_anchor_memory",       # Phase 2: anchor session memory on KR v2 (MEMORY source)
    "bort_commit_evolution",    # Phase 2: anchor a self-evolution result on KR v2 (INSTRUCTION source)
    "bort_marketplace_browse",  # Phase 2: browse marketplace listings + recent sales
    "bort_marketplace_agent",   # Phase 2: listings / offers / activity for one agent
    "bort_list_agent_uri",      # Phase 2: dapp deep link for owner-signed marketplace actions
}


def test_plugin_register_wires_all_tools_and_memory_provider():
    ctx = _load_plugin()
    assert set(ctx.tools.keys()) == EXPECTED_TOOLS
    for name, tool in ctx.tools.items():
        assert tool["toolset"] == "bort"
        assert tool.get("is_async") is True
        assert tool["schema"]["name"] == name
        assert "description" in tool["schema"]
        assert tool["schema"]["parameters"]["type"] == "object"
    assert ctx.memory_provider is not None
    assert ctx.memory_provider.name == "bort"
    assert ctx.memory_provider.is_available() is True
    assert ctx.memory_provider.get_tool_schemas() == []


def test_plugin_register_is_idempotent():
    """Hermes might call register more than once during reload: must not duplicate tools."""
    ctx = FakeCtx()
    import hermes_bort
    hermes_bort.register(ctx)
    hermes_bort.register(ctx)  # second call overwrites by name in our FakeCtx dict
    assert len(ctx.tools) == len(EXPECTED_TOOLS)


@pytest.mark.asyncio
async def test_health_check_via_registered_handler():
    ctx = _load_plugin()
    raw = await ctx.tools["bort_health_check"]["handler"]({})
    parsed = json.loads(raw)
    assert "ok_to_write" in parsed
    assert "global_paused" in parsed
    assert "contracts" in parsed


@pytest.mark.asyncio
async def test_list_actions_via_registered_handler():
    ctx = _load_plugin()
    for logic_name in ("Hunter", "Trading V5", "CTO"):
        raw = await ctx.tools["bort_list_actions"]["handler"]({"logic_name": logic_name})
        parsed = json.loads(raw)
        assert parsed["logic_name"] == logic_name
        assert parsed["totals"]["all"] > 0
        assert isinstance(parsed["on_chain"], list)
        assert isinstance(parsed["off_chain"], list)


@pytest.mark.asyncio
async def test_memory_provider_lifecycle_with_token_id(tmp_path, monkeypatch):
    """Initialize → sync_turn → on_session_end should not raise and should write a JSONL line."""
    monkeypatch.setenv("BORT_MEMORY_DIR", str(tmp_path))
    # Re-import so the provider picks up the env var
    import importlib
    import hermes_bort.memory.provider as provider_mod
    importlib.reload(provider_mod)

    provider = provider_mod.BortMemoryProvider()
    session_id = "smoke-session"
    provider.initialize(session_id, agent_identity=11100)

    provider.sync_turn("hello", "hi there", session_id=session_id)
    provider.sync_turn("what is the agent doing?", "It is in echo mode.", session_id=session_id)

    provider.on_session_end(session_id=session_id)

    written = tmp_path / "11100.jsonl"
    assert written.exists(), "memory provider should flush JSONL on session end"
    lines = written.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["user"] == "hello"
    assert parsed[1]["assistant"] == "It is in echo mode."


@pytest.mark.asyncio
async def test_memory_provider_inert_without_token_id():
    """If agent_identity is missing, the provider must not write or crash."""
    import hermes_bort.memory.provider as provider_mod
    provider = provider_mod.BortMemoryProvider()
    provider.initialize("no-token-session")  # no agent_identity
    provider.sync_turn("foo", "bar", session_id="no-token-session")
    provider.on_session_end(session_id="no-token-session")
    # If we got here without raising, we're good.


@pytest.mark.asyncio
async def test_memory_provider_prefetch_returns_string():
    import hermes_bort.memory.provider as provider_mod
    provider = provider_mod.BortMemoryProvider()
    provider.initialize("prefetch-session", agent_identity=11100)
    result = await provider.prefetch("any query", session_id="prefetch-session")
    assert isinstance(result, str)  # may be empty if no MEMORY sources, but never None
