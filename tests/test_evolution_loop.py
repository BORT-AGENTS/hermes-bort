"""Tests for hermes_bort.evolution_loop.

No network, no chain, no real optimizer: the subprocess and the two on-chain tool
handlers are all mocked. Filesystem fixtures use tmp_path.
"""
from __future__ import annotations

import json
import sys

import pytest

from hermes_bort import bort_chain, bort_ipfs, bort_signer, evolution_loop
import hermes_bort.tools.commit_evolution as ce_mod
import hermes_bort.tools.commit_learning as cl_mod


# ----- valid_skill_name -----
def test_valid_skill_name():
    assert evolution_loop.valid_skill_name("github-code-review")
    assert evolution_loop.valid_skill_name("skill_1.2")
    assert not evolution_loop.valid_skill_name("bad name")
    assert not evolution_loop.valid_skill_name("../escape")
    assert not evolution_loop.valid_skill_name("")


# ----- locate_self_evolution_repo -----
def _make_repo(root):
    """Create a minimal fake self-evolution repo with the optimizer entry file."""
    entry = root / "evolution" / "skills"
    entry.mkdir(parents=True)
    (entry / "evolve_skill.py").write_text("# fake optimizer\n", encoding="utf-8")
    return root


def test_locate_repo_explicit(tmp_path):
    repo = _make_repo(tmp_path / "se")
    assert evolution_loop.locate_self_evolution_repo(str(repo)) == repo


def test_locate_repo_rejects_dir_without_entry(tmp_path):
    # A dir lacking evolution/skills/evolve_skill.py is never returned as the match
    # (the function may still fall through to other candidates, but never to `bare`).
    bare = tmp_path / "not-a-repo"
    bare.mkdir()
    assert evolution_loop.locate_self_evolution_repo(str(bare)) != bare


def test_locate_repo_env_var(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path / "se")
    monkeypatch.setenv(evolution_loop.ENV_REPO, str(repo))
    assert evolution_loop.locate_self_evolution_repo() == repo


def test_locate_repo_result_is_always_valid(tmp_path, monkeypatch):
    # Whatever it returns must be None or a real repo with the optimizer entry file —
    # never a bogus path. (A real sibling repo may exist on a dev machine.)
    monkeypatch.delenv(evolution_loop.ENV_REPO, raising=False)
    result = evolution_loop.locate_self_evolution_repo(str(tmp_path / "nope"))
    assert result is None or (result / "evolution" / "skills" / "evolve_skill.py").is_file()


# ----- resolve_python -----
def test_resolve_python_env_var_wins(tmp_path, monkeypatch):
    monkeypatch.setenv(evolution_loop.ENV_PYTHON, "/custom/python")
    assert evolution_loop.resolve_python(tmp_path) == "/custom/python"


def test_resolve_python_repo_venv(tmp_path, monkeypatch):
    monkeypatch.delenv(evolution_loop.ENV_PYTHON, raising=False)
    # create a fake venv interpreter (either layout)
    win = tmp_path / ".venv" / "Scripts"
    nix = tmp_path / ".venv" / "bin"
    win.mkdir(parents=True)
    nix.mkdir(parents=True)
    (win / "python.exe").write_text("", encoding="utf-8")
    (nix / "python").write_text("", encoding="utf-8")
    resolved = evolution_loop.resolve_python(tmp_path)
    assert ".venv" in resolved
    # never falls back to hermes-bort's own interpreter
    assert resolved != sys.executable


def test_resolve_python_falls_back_to_path(tmp_path, monkeypatch):
    monkeypatch.delenv(evolution_loop.ENV_PYTHON, raising=False)
    resolved = evolution_loop.resolve_python(tmp_path)  # no .venv
    assert resolved  # "python" or a which() hit, but never crashes
    assert resolved != sys.executable


# ----- snapshot / newest_new_run_dir / latest_existing_run_dir -----
def test_snapshot_and_diff(tmp_path):
    repo, skill = tmp_path, "myskill"
    out = repo / "output" / skill
    out.mkdir(parents=True)
    (out / "20260101_000000").mkdir()
    before = evolution_loop.snapshot_run_dirs(repo, skill)
    assert before == {"20260101_000000"}
    (out / "20260102_000000").mkdir()
    new_dir, note = evolution_loop.newest_new_run_dir(repo, skill, before)
    assert new_dir == out / "20260102_000000"
    assert note == ""


def test_newest_new_run_dir_none(tmp_path):
    repo, skill = tmp_path, "myskill"
    (repo / "output" / skill).mkdir(parents=True)
    before = evolution_loop.snapshot_run_dirs(repo, skill)
    new_dir, note = evolution_loop.newest_new_run_dir(repo, skill, before)
    assert new_dir is None
    assert "no new run dir" in note


def test_newest_new_run_dir_multiple(tmp_path):
    repo, skill = tmp_path, "myskill"
    out = repo / "output" / skill
    out.mkdir(parents=True)
    before = evolution_loop.snapshot_run_dirs(repo, skill)
    (out / "20260101_000000").mkdir()
    (out / "20260103_000000").mkdir()
    new_dir, note = evolution_loop.newest_new_run_dir(repo, skill, before)
    assert new_dir == out / "20260103_000000"
    assert "multiple" in note


def test_latest_existing_run_dir(tmp_path):
    repo, skill = tmp_path, "myskill"
    out = repo / "output" / skill
    out.mkdir(parents=True)
    (out / "20260101_000000").mkdir()
    (out / "20260102_000000").mkdir()
    assert evolution_loop.latest_existing_run_dir(repo, skill) == out / "20260102_000000"
    assert evolution_loop.latest_existing_run_dir(repo, "other") is None


def test_has_failed_marker(tmp_path):
    repo, skill = tmp_path, "myskill"
    out = repo / "output" / skill
    out.mkdir(parents=True)
    assert not evolution_loop.has_failed_marker(repo, skill)
    (out / "evolved_FAILED.md").write_text("# failed\n", encoding="utf-8")
    assert evolution_loop.has_failed_marker(repo, skill)


# ----- evolved_skill_hash -----
def test_evolved_skill_hash(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "evolved_skill.md").write_text("# evolved\n", encoding="utf-8")
    h = evolution_loop.evolved_skill_hash(run_dir)
    assert h.startswith("0x")
    assert len(h) == 66  # 0x + 64 hex


# ----- build_optimizer_cmd -----
def test_build_optimizer_cmd():
    cmd = evolution_loop.build_optimizer_cmd("python", "myskill", 7)
    assert cmd[:3] == ["python", "-m", "evolution.skills.evolve_skill"]
    assert "--skill" in cmd and "myskill" in cmd
    assert "--iterations" in cmd and "7" in cmd


# ----- run_optimizer -----
class _FakeProc:
    def __init__(self, returncode):
        self.returncode = returncode


def test_run_optimizer_success(tmp_path, monkeypatch):
    repo, skill = tmp_path, "myskill"
    (repo / "output" / skill).mkdir(parents=True)

    def fake_run(cmd, cwd=None, timeout=None, env=None):
        d = repo / "output" / skill / "20260518_120000"
        d.mkdir(parents=True)
        (d / "evolved_skill.md").write_text("# evolved\n", encoding="utf-8")
        (d / "metrics.json").write_text(
            json.dumps({"iterations": 7, "improvement": 0.12}), encoding="utf-8")
        return _FakeProc(0)

    monkeypatch.setattr(evolution_loop.subprocess, "run", fake_run)
    run = evolution_loop.run_optimizer(repo, "python", skill, 7)
    assert run.error is None
    assert run.run_dir is not None
    assert run.metrics["improvement"] == 0.12
    assert run.failed_marker is False


def test_run_optimizer_failed_marker(tmp_path, monkeypatch):
    repo, skill = tmp_path, "myskill"
    (repo / "output" / skill).mkdir(parents=True)

    def fake_run(cmd, cwd=None, timeout=None, env=None):
        (repo / "output" / skill / "evolved_FAILED.md").write_text("# fail\n", encoding="utf-8")
        return _FakeProc(0)

    monkeypatch.setattr(evolution_loop.subprocess, "run", fake_run)
    run = evolution_loop.run_optimizer(repo, "python", skill, 1)
    assert run.run_dir is None
    assert run.failed_marker is True
    assert run.error is None


def test_run_optimizer_nonzero_exit(tmp_path, monkeypatch):
    repo, skill = tmp_path, "myskill"
    (repo / "output" / skill).mkdir(parents=True)
    monkeypatch.setattr(evolution_loop.subprocess, "run",
                        lambda cmd, cwd=None, timeout=None, env=None: _FakeProc(1))
    run = evolution_loop.run_optimizer(repo, "python", skill, 1)
    assert run.error is not None
    assert "exited with code 1" in run.error


def test_run_optimizer_interpreter_missing(tmp_path, monkeypatch):
    def boom(cmd, cwd=None, timeout=None, env=None):
        raise FileNotFoundError("no such interpreter")

    monkeypatch.setattr(evolution_loop.subprocess, "run", boom)
    run = evolution_loop.run_optimizer(tmp_path, "nopython", "skill", 1)
    assert run.error is not None
    assert "interpreter not found" in run.error


def test_run_optimizer_passes_model_flags(tmp_path, monkeypatch):
    repo, skill = tmp_path, "myskill"
    (repo / "output" / skill).mkdir(parents=True)
    captured = {}

    def fake_run(cmd, cwd=None, timeout=None, env=None):
        captured["cmd"] = cmd
        return _FakeProc(0)

    monkeypatch.setattr(evolution_loop.subprocess, "run", fake_run)
    evolution_loop.run_optimizer(
        repo, "python", skill, 2,
        optimizer_model="anthropic/claude-sonnet-4-6", eval_model="deepseek/deepseek-chat")
    cmd = captured["cmd"]
    assert "--optimizer-model" in cmd and "anthropic/claude-sonnet-4-6" in cmd
    assert "--eval-model" in cmd and "deepseek/deepseek-chat" in cmd


# ----- preflight_check -----
@pytest.mark.asyncio
async def test_preflight_dry_run_ok(monkeypatch):
    monkeypatch.delenv("BORT_ALLOW_BROADCAST", raising=False)
    monkeypatch.setattr(bort_ipfs, "pinata_configured", lambda: True)
    pf = await evolution_loop.preflight_check(11100)
    assert pf["ok"] is True
    assert pf["broadcast"] is False


@pytest.mark.asyncio
async def test_preflight_no_pinata(monkeypatch):
    monkeypatch.delenv("BORT_ALLOW_BROADCAST", raising=False)
    monkeypatch.setattr(bort_ipfs, "pinata_configured", lambda: False)
    pf = await evolution_loop.preflight_check(11100)
    assert pf["ok"] is False
    assert any("PINATA" in p for p in pf["problems"])


@pytest.mark.asyncio
async def test_preflight_broadcast_no_operator(monkeypatch):
    monkeypatch.setenv("BORT_ALLOW_BROADCAST", "1")
    monkeypatch.setattr(bort_ipfs, "pinata_configured", lambda: True)

    def raise_missing():
        raise bort_signer.OperatorKeyMissing("not set")

    monkeypatch.setattr(bort_signer, "operator_address", raise_missing)
    pf = await evolution_loop.preflight_check(11100)
    assert pf["ok"] is False
    assert any("BORT_OPERATOR_PRIVATE_KEY" in p for p in pf["problems"])


@pytest.mark.asyncio
async def test_preflight_broadcast_all_ready(monkeypatch):
    monkeypatch.setenv("BORT_ALLOW_BROADCAST", "1")
    monkeypatch.setattr(bort_ipfs, "pinata_configured", lambda: True)
    monkeypatch.setattr(bort_signer, "operator_address", lambda: "0xOperator")

    async def fake_balance():
        return 0.01

    async def fake_can_forward(tid, vid, op):
        return True

    monkeypatch.setattr(bort_signer, "operator_balance_bnb", fake_balance)
    monkeypatch.setattr(bort_chain, "can_forward", fake_can_forward)
    pf = await evolution_loop.preflight_check(11100)
    assert pf["ok"] is True
    assert pf["broadcast"] is True


@pytest.mark.asyncio
async def test_preflight_broadcast_no_grant(monkeypatch):
    monkeypatch.setenv("BORT_ALLOW_BROADCAST", "1")
    monkeypatch.setattr(bort_ipfs, "pinata_configured", lambda: True)
    monkeypatch.setattr(bort_signer, "operator_address", lambda: "0xOperator")

    async def fake_balance():
        return 0.01

    async def fake_can_forward(tid, vid, op):
        return False

    monkeypatch.setattr(bort_signer, "operator_balance_bnb", fake_balance)
    monkeypatch.setattr(bort_chain, "can_forward", fake_can_forward)
    pf = await evolution_loop.preflight_check(11100)
    assert pf["ok"] is False
    assert any("WRITE permission" in p for p in pf["problems"])


@pytest.mark.asyncio
async def test_preflight_learning_only_skips_pinata(monkeypatch):
    # learning-only commits no content, so a missing Pinata key must not block it.
    monkeypatch.delenv("BORT_ALLOW_BROADCAST", raising=False)
    monkeypatch.setattr(bort_ipfs, "pinata_configured", lambda: False)
    pf = await evolution_loop.preflight_check(11100, only="learning")
    assert pf["ok"] is True
    assert not any("PINATA" in p for p in pf["problems"])


# ----- chain_commits -----
def _run_dir_with(tmp_path, *, iterations=5, body="# evolved\n"):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "evolved_skill.md").write_text(body, encoding="utf-8")
    (run_dir / "metrics.json").write_text(
        json.dumps({"iterations": iterations, "improvement": 0.1}), encoding="utf-8")
    return run_dir


@pytest.mark.asyncio
async def test_chain_commits_both_ok(tmp_path, monkeypatch):
    run_dir = _run_dir_with(tmp_path, iterations=9)
    chash = "0x" + "ab" * 32
    learn_args = {}

    async def fake_ce(args):
        return json.dumps({"status": "ok", "content_hash": chash, "pinned_cid": "Qm1"})

    async def fake_cl(args):
        learn_args.update(args)
        return json.dumps({"status": "ok", "tx_hash": "0xdead"})

    monkeypatch.setattr(ce_mod, "handle", fake_ce)
    monkeypatch.setattr(cl_mod, "handle", fake_cl)
    result = await evolution_loop.chain_commits(11100, run_dir, only="both")
    assert result["chained"] is True
    assert result["content_hash"] == chash
    assert learn_args["data_hash"] == chash
    assert learn_args["interaction_count"] == 9
    assert "fully committed" in result["summary"]


@pytest.mark.asyncio
async def test_chain_commits_evolution_fails_skips_learning(tmp_path, monkeypatch):
    run_dir = _run_dir_with(tmp_path)
    cl_called = {"yes": False}

    async def fake_ce(args):
        return json.dumps({"error": "PINATA_API_KEY not set"})

    async def fake_cl(args):
        cl_called["yes"] = True
        return json.dumps({"status": "ok"})

    monkeypatch.setattr(ce_mod, "handle", fake_ce)
    monkeypatch.setattr(cl_mod, "handle", fake_cl)
    result = await evolution_loop.chain_commits(11100, run_dir, only="both")
    assert result["chained"] is False
    assert result["learning"] is None
    assert cl_called["yes"] is False


@pytest.mark.asyncio
async def test_chain_commits_partial(tmp_path, monkeypatch):
    run_dir = _run_dir_with(tmp_path)

    async def fake_ce(args):
        return json.dumps({"status": "ok", "content_hash": "0x" + "cd" * 32})

    async def fake_cl(args):
        return json.dumps({"status": "blocked", "reason": "no permission"})

    monkeypatch.setattr(ce_mod, "handle", fake_ce)
    monkeypatch.setattr(cl_mod, "handle", fake_cl)
    result = await evolution_loop.chain_commits(11100, run_dir, only="both")
    assert "PARTIAL" in result["summary"]


@pytest.mark.asyncio
async def test_chain_commits_both_simulated(tmp_path, monkeypatch):
    run_dir = _run_dir_with(tmp_path)

    async def fake_ce(args):
        return json.dumps({"status": "simulated_only", "content_hash": "0x" + "ef" * 32})

    async def fake_cl(args):
        return json.dumps({"status": "simulated_only"})

    monkeypatch.setattr(ce_mod, "handle", fake_ce)
    monkeypatch.setattr(cl_mod, "handle", fake_cl)
    result = await evolution_loop.chain_commits(11100, run_dir, only="both")
    assert "dry run" in result["summary"]


@pytest.mark.asyncio
async def test_chain_commits_only_evolution(tmp_path, monkeypatch):
    run_dir = _run_dir_with(tmp_path)
    cl_called = {"yes": False}

    async def fake_ce(args):
        return json.dumps({"status": "ok", "content_hash": "0x" + "11" * 32})

    async def fake_cl(args):
        cl_called["yes"] = True
        return json.dumps({"status": "ok"})

    monkeypatch.setattr(ce_mod, "handle", fake_ce)
    monkeypatch.setattr(cl_mod, "handle", fake_cl)
    result = await evolution_loop.chain_commits(11100, run_dir, only="evolution")
    assert cl_called["yes"] is False
    assert result["learning"] is None
    assert result["content_hash"] == "0x" + "11" * 32


@pytest.mark.asyncio
async def test_chain_commits_only_learning(tmp_path, monkeypatch):
    run_dir = _run_dir_with(tmp_path, iterations=3)
    ce_called = {"yes": False}
    learn_args = {}

    async def fake_ce(args):
        ce_called["yes"] = True
        return json.dumps({"status": "ok"})

    async def fake_cl(args):
        learn_args.update(args)
        return json.dumps({"status": "ok"})

    monkeypatch.setattr(ce_mod, "handle", fake_ce)
    monkeypatch.setattr(cl_mod, "handle", fake_cl)
    result = await evolution_loop.chain_commits(11100, run_dir, only="learning")
    assert ce_called["yes"] is False
    assert result["evolution"] is None
    # learning-only recomputes the hash from evolved_skill.md
    assert learn_args["data_hash"] == evolution_loop.evolved_skill_hash(run_dir)
    assert learn_args["interaction_count"] == 3
