"""BortMemoryProvider: IPFS-backed (read) + local-JSONL (write) memory for Hermes Agent.

Phase 1 design:
- READ: pull MEMORY-type sources (sourceType=2) from KnowledgeRegistry, fetch each via
  IPFS, replay turns into context. Falls back to local JSONL if KnowledgeRegistry has
  nothing for this tokenId.
- WRITE: append turns to local `~/.hermes/bort-memory/{token_id}.jsonl`. On shutdown,
  flush is local-only: no on-chain anchor in Phase 1. Phase 1.5 promotes the write
  path to either KnowledgeRegistry.addKnowledgeSource(MEMORY) or
  handleAction("record_learning", ...).

The plugin scopes memory by tokenId via the `agent_identity` kwarg passed by Hermes at
session initialize. If no token_id is set, the provider is inert.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Iterable

from .. import bort_chain, bort_ipfs
from ..bort_sanitize import wrap_external


def _memory_dir() -> Path:
    raw = os.environ.get("BORT_MEMORY_DIR", "~/.hermes/bort-memory")
    return Path(os.path.expanduser(raw))


def _coerce_token_id(agent_identity: Any) -> int | None:
    if agent_identity is None:
        return None
    if isinstance(agent_identity, int):
        return agent_identity
    s = str(agent_identity).strip()
    if not s:
        return None
    # Allow either bare integer or "bort:11100" / "bap-578:11100" prefixed forms
    for sep in (":", "/", "-", "_"):
        if sep in s:
            tail = s.rsplit(sep, 1)[-1]
            if tail.isdigit():
                return int(tail)
    return int(s) if s.isdigit() else None


class BortMemoryProvider:
    """Hermes MemoryProvider implementation, NFT-bound by tokenId."""

    name: str = "bort"
    description: str = "BORT (BAP-578) NFT-bound memory: IPFS read + local JSONL write."

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buffers: dict[str, list[dict[str, Any]]] = {}
        self._token_by_session: dict[str, int] = {}
        self._dir = _memory_dir()

    # ---- Capability checks --------------------------------------------------------
    def is_available(self) -> bool:
        # Always available: read path is best-effort, write path is local-only.
        return True

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        # No memory-management tools exposed in Phase 1.
        return []

    def system_prompt_block(self) -> str:
        return ""

    # ---- Lifecycle ----------------------------------------------------------------
    def initialize(self, session_id: str, **kwargs) -> None:
        agent_identity = kwargs.get("agent_identity")
        token_id = _coerce_token_id(agent_identity)
        if token_id is None:
            return
        with self._lock:
            self._token_by_session[session_id] = token_id
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def shutdown(self) -> None:
        with self._lock:
            buffers = list(self._buffers.items())
            tokens = dict(self._token_by_session)
            self._buffers.clear()
            self._token_by_session.clear()
        for session_id, buf in buffers:
            token_id = tokens.get(session_id)
            if token_id is None or not buf:
                continue
            self._append_local(token_id, buf)

    def on_session_end(self, session_id: str = "", **kwargs) -> None:
        with self._lock:
            buf = self._buffers.pop(session_id, None)
            token_id = self._token_by_session.pop(session_id, None)
        if token_id is not None and buf:
            self._append_local(token_id, buf)

    # ---- Read --------------------------------------------------------------------
    async def prefetch(self, query: str, *, session_id: str = "", **kwargs) -> str:
        token_id = self._token_by_session.get(session_id)
        if token_id is None:
            token_id = _coerce_token_id(kwargs.get("agent_identity"))
        if token_id is None:
            return ""

        on_chain_blocks = await self._read_on_chain(token_id)
        if on_chain_blocks:
            return self._format_blocks(on_chain_blocks, source="on-chain")

        local_blocks = self._read_local(token_id)
        if local_blocks:
            return self._format_blocks(local_blocks, source="local")
        return ""

    def queue_prefetch(self, query: str, *, session_id: str = "", **kwargs) -> None:
        # Optional ABC method. No-op in Phase 1: prefetch is fast enough.
        return None

    # ---- Write -------------------------------------------------------------------
    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        **kwargs,
    ) -> None:
        token_id = self._token_by_session.get(session_id)
        if token_id is None:
            token_id = _coerce_token_id(kwargs.get("agent_identity"))
            if token_id is not None:
                with self._lock:
                    self._token_by_session[session_id] = token_id
        if token_id is None:
            return
        with self._lock:
            buf = self._buffers.setdefault(session_id, [])
            buf.append({
                "user":       user_content,
                "assistant":  assistant_content,
                "session_id": session_id,
            })

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs) -> Any:
        # No memory tools in Phase 1.
        return None

    # ---- Helpers -----------------------------------------------------------------
    async def _read_on_chain(self, token_id: int) -> list[dict[str, Any]]:
        try:
            sources = await bort_chain.get_knowledge_sources(token_id, active_only=True)
        except Exception:
            return []
        memory_sources = [s for s in sources if s.get("source_type_id") == 2]
        if not memory_sources:
            return []
        memory_sources.sort(key=lambda s: int(s.get("priority") or 0), reverse=True)

        blocks: list[dict[str, Any]] = []
        for src in memory_sources[:5]:  # top 5 by priority
            uri = src.get("uri")
            if not uri:
                continue
            payload = await bort_ipfs.fetch_json(uri)
            if not payload:
                continue
            blocks.append({
                "source":      "knowledge_registry",
                "id":          src.get("id"),
                "priority":    src.get("priority"),
                "description": src.get("description"),
                "payload":     payload,
            })
        return blocks

    def _read_local(self, token_id: int) -> list[dict[str, Any]]:
        path = self._dir / f"{token_id}.jsonl"
        if not path.exists():
            return []
        blocks: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        blocks.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return []
        return blocks[-50:]

    def _append_local(self, token_id: int, turns: Iterable[dict[str, Any]]) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        path = self._dir / f"{token_id}.jsonl"
        try:
            with path.open("a", encoding="utf-8") as fh:
                for t in turns:
                    fh.write(json.dumps(t, ensure_ascii=False) + "\n")
        except OSError:
            return

    @staticmethod
    def _format_blocks(blocks: list[dict[str, Any]], *, source: str) -> str:
        """Render KR memory blocks (or local JSONL fallback) for prefetch.

        Each block payload is owner-controlled (KR) or previously-recorded chat
        (local). Both are treated as untrusted: wrapped in <external-data>
        envelopes with control-char strip + length cap. See bort_sanitize.
        """
        if not blocks:
            return ""
        lines = [f"[BORT memory · {source}]"]
        for block in blocks:
            payload = block.get("payload") if "payload" in block else block
            block_id = block.get("id") if isinstance(block, dict) else None
            label = f"bort-memory:{source}" + (f":{block_id}" if block_id is not None else "")
            text = json.dumps(payload, ensure_ascii=False, default=str)
            lines.append(wrap_external(text, source=label))
        return "\n".join(lines)
