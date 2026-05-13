"""bort_commit_evolution: anchor a hermes-agent-self-evolution result on-chain.

After `python -m evolution.skills.evolve_skill --skill X --iterations N`, the optimizer
writes `output/<skill>/<timestamp>/{evolved_skill.md, baseline_skill.md, metrics.json}`.
This tool pins `evolved_skill.md` to IPFS and writes the CID as an INSTRUCTION-type
knowledge source on KnowledgeRegistryV2, so the evolved capability lives with the NFT.

Requires: PINATA_API_KEY/PINATA_API_SECRET, operator key with WRITE permission on the
agent's vault, BORT_ALLOW_BROADCAST=1 for the real write.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from eth_utils import keccak

from .. import bort_ipfs, bort_kr


SCHEMA = {
    "name": "bort_commit_evolution",
    "description": (
        "Anchor a hermes-agent-self-evolution result on-chain. Reads the evolved skill from the "
        "optimizer's output dir (output/<skill>/<timestamp>/evolved_skill.md), pins it to IPFS, "
        "and writes the CID as an INSTRUCTION-type knowledge source on KnowledgeRegistryV2 so the "
        "evolved capability is bound to the NFT. Requires Pinata credentials, the operator key "
        "with WRITE permission on the agent's vault, and BORT_ALLOW_BROADCAST=1."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "token_id":   {"type": "integer", "description": "BAP-578 NFT token ID to anchor the evolved skill to."},
            "output_dir": {"type": "string",  "description": "Path to the self-evolution output dir, e.g. output/github-code-review/2026-05-12T.../"},
            "priority":   {"type": "integer", "default": 50, "description": "Knowledge-source priority. Default 50."},
        },
        "required": ["token_id", "output_dir"],
    },
}


def _summarize_metrics(metrics: dict[str, Any], fallback_name: str) -> str:
    """Best-effort short summary from metrics.json (schema varies across self-evolution versions)."""
    skill = metrics.get("skill") or metrics.get("skill_name") or metrics.get("name") or fallback_name
    base = metrics.get("baseline_score", metrics.get("baseline"))
    evo  = metrics.get("evolved_score", metrics.get("evolved", metrics.get("best_score")))
    pct  = metrics.get("improvement_pct", metrics.get("improvement"))
    parts = [f"Evolved skill: {skill}"]
    if base is not None and evo is not None:
        parts.append(f"({base} -> {evo})")
    if pct is not None:
        parts.append(f"+{pct}%")
    return " ".join(str(p) for p in parts)[:480]


async def handle(args: dict[str, Any], **kwargs) -> str:
    token_id = int(args["token_id"])
    priority = int(args.get("priority", 50))
    out_dir = Path(os.path.expanduser(str(args["output_dir"])))

    if not out_dir.is_dir():
        return json.dumps({"error": f"output dir not found: {out_dir}", "tool": "bort_commit_evolution"})

    evolved = out_dir / "evolved_skill.md"
    if not evolved.exists():
        if (out_dir / "evolved_FAILED.md").exists():
            return json.dumps({
                "error": f"this run produced evolved_FAILED.md (the variant didn't pass guardrails): nothing to anchor: {out_dir / 'evolved_FAILED.md'}",
                "tool":  "bort_commit_evolution",
            })
        return json.dumps({"error": f"no evolved_skill.md in {out_dir}", "tool": "bort_commit_evolution"})

    skill_bytes = evolved.read_bytes()
    if not skill_bytes.strip():
        return json.dumps({"error": "evolved_skill.md is empty", "tool": "bort_commit_evolution"})

    metrics: dict[str, Any] = {}
    metrics_path = out_dir / "metrics.json"
    if metrics_path.exists():
        try:
            loaded = json.loads(metrics_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                metrics = loaded
        except Exception:  # noqa: BLE001
            metrics = {}
    skill_name = out_dir.parent.name  # output/<skill>/<timestamp> → <skill>
    desc = _summarize_metrics(metrics, skill_name) if metrics else f"Evolved skill: {skill_name}"

    if not bort_ipfs.pinata_configured():
        return json.dumps({
            "error": "PINATA_API_KEY / PINATA_API_SECRET not set: cannot pin the evolved skill to IPFS.",
            "tool":  "bort_commit_evolution",
        })

    cid = await bort_ipfs.pin_bytes(skill_bytes, f"evolved-skill-{skill_name}.md")
    if cid is None:
        return json.dumps({"error": "Pinata pin failed", "tool": "bort_commit_evolution"})

    content_hash = keccak(skill_bytes)
    result = await bort_kr.write_knowledge_source_delegated(
        token_id, f"ipfs://{cid}", bort_kr.KT_INSTRUCTION,
        priority=priority, description=desc, content_hash=content_hash,
    )
    result["tool"] = "bort_commit_evolution"
    result["pinned_cid"] = cid
    result["evolved_skill_bytes"] = len(skill_bytes)
    result["skill_name"] = skill_name
    if metrics:
        result["metrics"] = metrics
    return json.dumps(result, default=str, ensure_ascii=False)


def register_commit_evolution(ctx) -> None:
    ctx.register_tool(
        name="bort_commit_evolution",
        toolset="bort",
        schema=SCHEMA,
        handler=handle,
        is_async=True,
        description="Anchor a self-evolution result on KR v2 as an INSTRUCTION source.",
        emoji="🧬",
    )
