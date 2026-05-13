"""Tests for bort_anchor_memory and bort_commit_evolution tools.

Without Pinata creds (test env), both tools short-circuit with a clear error before
any chain interaction. With a fabricated input + no creds we verify the error path;
the on-chain anchor itself is covered by test_bort_kr.py.
"""
from __future__ import annotations

import json
import os

import pytest

from hermes_bort.tools.anchor_memory import handle as anchor_memory
from hermes_bort.tools.commit_evolution import handle as commit_evolution


TEST_TOKEN_ID = int(os.environ.get("BORT_TEST_TOKEN_ID", "11100"))


# ----- bort_anchor_memory -----
@pytest.mark.asyncio
async def test_anchor_memory_no_local_file(tmp_path, monkeypatch):
    monkeypatch.setenv("BORT_MEMORY_DIR", str(tmp_path))
    raw = await anchor_memory({"token_id": 999999})
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "no local memory file" in parsed["error"]


@pytest.mark.asyncio
async def test_anchor_memory_empty_file(tmp_path, monkeypatch):
    monkeypatch.setenv("BORT_MEMORY_DIR", str(tmp_path))
    (tmp_path / "12345.jsonl").write_text("   \n", encoding="utf-8")
    raw = await anchor_memory({"token_id": 12345})
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "empty" in parsed["error"]


@pytest.mark.asyncio
async def test_anchor_memory_no_pinata_creds(tmp_path, monkeypatch):
    monkeypatch.setenv("BORT_MEMORY_DIR", str(tmp_path))
    monkeypatch.delenv("PINATA_API_KEY", raising=False)
    monkeypatch.delenv("PINATA_API_SECRET", raising=False)
    (tmp_path / "12345.jsonl").write_text(
        json.dumps({"user": "hi", "assistant": "hello"}) + "\n", encoding="utf-8",
    )
    raw = await anchor_memory({"token_id": 12345})
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "PINATA_API_KEY" in parsed["error"]


# ----- bort_commit_evolution -----
@pytest.mark.asyncio
async def test_commit_evolution_output_dir_missing():
    raw = await commit_evolution({"token_id": TEST_TOKEN_ID, "output_dir": "/no/such/dir/xyz"})
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "output dir not found" in parsed["error"]


@pytest.mark.asyncio
async def test_commit_evolution_failed_variant(tmp_path):
    run_dir = tmp_path / "github-code-review" / "2026-05-12T00-00-00"
    run_dir.mkdir(parents=True)
    (run_dir / "evolved_FAILED.md").write_text("# failed variant\n", encoding="utf-8")
    raw = await commit_evolution({"token_id": TEST_TOKEN_ID, "output_dir": str(run_dir)})
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "evolved_FAILED.md" in parsed["error"]


@pytest.mark.asyncio
async def test_commit_evolution_no_evolved_md(tmp_path):
    run_dir = tmp_path / "some-skill" / "ts"
    run_dir.mkdir(parents=True)
    raw = await commit_evolution({"token_id": TEST_TOKEN_ID, "output_dir": str(run_dir)})
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "no evolved_skill.md" in parsed["error"]


@pytest.mark.asyncio
async def test_commit_evolution_no_pinata_creds(tmp_path, monkeypatch):
    monkeypatch.delenv("PINATA_API_KEY", raising=False)
    monkeypatch.delenv("PINATA_API_SECRET", raising=False)
    run_dir = tmp_path / "github-code-review" / "2026-05-12T00-00-00"
    run_dir.mkdir(parents=True)
    (run_dir / "evolved_skill.md").write_text("# github-code-review v2\n\nImproved.\n", encoding="utf-8")
    (run_dir / "metrics.json").write_text(
        json.dumps({"skill": "github-code-review", "baseline_score": 0.71, "evolved_score": 0.83, "improvement_pct": 16.9}),
        encoding="utf-8",
    )
    raw = await commit_evolution({"token_id": TEST_TOKEN_ID, "output_dir": str(run_dir)})
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "PINATA_API_KEY" in parsed["error"]
