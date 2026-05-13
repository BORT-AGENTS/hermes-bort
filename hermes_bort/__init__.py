"""hermes-bort: BORT (BAP-578) plugin for Hermes Agent.

Phase 1: read-only tools + read-portable memory provider.

Hermes plugin contract:
- `register(ctx)` is the only entry point
- Tools register through `ctx.register_tool(...)`
- Memory provider through `ctx.register_memory_provider(...)`
"""
from __future__ import annotations

__version__ = "0.1.0"


def register(ctx):
    """Hermes plugin entry point. Called once by PluginManager on load.

    Phase 1 reads:    read_agent / health_check / list_actions.
    Phase 1.5a:       grant_permission_uri / commit_learning.
    Phase 1.5b:       invoke (universal write via VPMv2.forwardHandleAction).
    Phase 2:          anchor_memory / commit_evolution (KR v2),
                      marketplace_browse / marketplace_agent / list_agent_uri.
    Memory provider registers only when ctx supports it (smoke tests; future
    memory-plugin install path).
    """
    from .tools.read_agent import register_read_agent
    from .tools.health_check import register_health_check
    from .tools.list_actions import register_list_actions
    from .tools.grant_permission import register_grant_permission
    from .tools.commit_learning import register_commit_learning
    from .tools.invoke import register_invoke
    from .tools.anchor_memory import register_anchor_memory
    from .tools.commit_evolution import register_commit_evolution
    from .tools.marketplace_browse import register_marketplace_browse
    from .tools.marketplace_agent import register_marketplace_agent
    from .tools.list_agent_uri import register_list_agent_uri
    from .cli import register_cli

    register_read_agent(ctx)
    register_health_check(ctx)
    register_list_actions(ctx)
    register_grant_permission(ctx)
    register_commit_learning(ctx)
    register_invoke(ctx)
    register_anchor_memory(ctx)
    register_commit_evolution(ctx)
    register_marketplace_browse(ctx)
    register_marketplace_agent(ctx)
    register_list_agent_uri(ctx)
    register_cli(ctx)

    if hasattr(ctx, "register_memory_provider"):
        from .memory.provider import BortMemoryProvider
        ctx.register_memory_provider(BortMemoryProvider())
