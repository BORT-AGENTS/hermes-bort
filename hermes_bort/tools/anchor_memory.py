"""bort_anchor_memory: pin the agent's local session memory to IPFS and write it as a
MEMORY-type knowledge source on KnowledgeRegistryV2, so memory travels with the NFT.

The plugin keeps session memory in ~/.hermes/bort-memory/{token_id}.jsonl. This tool
pins that file to Pinata and anchors the CID on-chain via the operator's WRITE permission.
BortMemoryProvider.prefetch already reads MEMORY sources back, so a fresh Hermes host
picks up the anchored memory.

Requires: PINATA_API_KEY/PINATA_API_SECRET, operator key with WRITE permission on the
agent's vault (via bort_grant_permission_uri), BORT_ALLOW_BROADCAST=1 for the real write.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from eth_utils import keccak

from .. import bort_ipfs, bort_kr


def _memory_dir() -> Path:
    raw = os.environ.get("BORT_MEMORY_DIR", "~/.hermes/bort-memory")
    return Path(os.path.expanduser(raw))


SCHEMA = {
    "name": "bort_anchor_memory",
    "description": (
        "Pin the agent's local session memory (~/.hermes/bort-memory/{token_id}.jsonl) to IPFS "
        "and anchor the CID as a MEMORY-type knowledge source on KnowledgeRegistryV2, so the "
        "memory travels with the NFT and any Hermes host can read it back. Requires Pinata "
        "credentials, the operator key with WRITE permission on the agent's vault, and "
        "BORT_ALLOW_BROADCAST=1 for the real write."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "token_id": {"type": "integer", "description": "BAP-578 NFT token ID."},
            "priority": {
                "type": "integer",
                "default": 10,
                "description": "Knowledge-source priority (higher = more important). Default 10.",
            },
        },
        "required": ["token_id"],
    },
}


async def handle(args: dict[str, Any], **kwargs) -> str:
    token_id = int(args["token_id"])
    priority = int(args.get("priority", 10))

    path = _memory_dir() / f"{token_id}.jsonl"
    if not path.exists():
        return json.dumps({"error": f"no local memory file at {path}: nothing to anchor", "tool": "bort_anchor_memory"})
    data = path.read_bytes()
    if not data.strip():
        return json.dumps({"error": "local memory file is empty", "tool": "bort_anchor_memory"})

    if not bort_ipfs.pinata_configured():
        return json.dumps({
            "error": "PINATA_API_KEY / PINATA_API_SECRET not set: cannot pin memory to IPFS. Set them and retry.",
            "tool":  "bort_anchor_memory",
        })

    cid = await bort_ipfs.pin_bytes(data, f"bort-memory-{token_id}.jsonl")
    if cid is None:
        return json.dumps({"error": "Pinata pin failed", "tool": "bort_anchor_memory"})

    content_hash = keccak(data)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    turns = len([ln for ln in data.decode("utf-8", "replace").splitlines() if ln.strip()])
    desc = f"Hermes session memory {ts} ({turns} turns)"

    result = await bort_kr.write_knowledge_source_delegated(
        token_id, f"ipfs://{cid}", bort_kr.KT_MEMORY,
        priority=priority, description=desc, content_hash=content_hash,
    )
    result["tool"] = "bort_anchor_memory"
    result["pinned_cid"] = cid
    result["turns"] = turns
    return json.dumps(result, default=str, ensure_ascii=False)


def register_anchor_memory(ctx) -> None:
    ctx.register_tool(
        name="bort_anchor_memory",
        toolset="bort",
        schema=SCHEMA,
        handler=handle,
        is_async=True,
        description="Pin local session memory to IPFS and anchor it on KR v2 as a MEMORY source.",
        emoji="💾",
    )
