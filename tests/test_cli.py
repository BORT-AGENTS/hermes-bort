"""Tests for the `hermes bort` CLI commands.

We don't run the real Hermes CLI here: we verify the command registers correctly
and that the argparse setup wires the three sub-subcommands. The handlers themselves
are exercised lightly (init-policy round-trip; init-operator output shape).
"""
from __future__ import annotations

import argparse
import io
import json
import os
from contextlib import redirect_stdout

import pytest

from hermes_bort import cli as bort_cli
from hermes_bort import evolution_loop


class _FakeCtx:
    def __init__(self):
        self.cli_commands = []

    def register_cli_command(self, *, name, help, setup_fn, handler_fn=None, description=""):
        self.cli_commands.append({
            "name": name, "help": help, "setup_fn": setup_fn,
            "handler_fn": handler_fn, "description": description,
        })


def test_register_cli_registers_bort_command():
    ctx = _FakeCtx()
    bort_cli.register_cli(ctx)
    assert len(ctx.cli_commands) == 1
    cmd = ctx.cli_commands[0]
    assert cmd["name"] == "bort"
    assert callable(cmd["setup_fn"])
    assert callable(cmd["handler_fn"])


def test_register_cli_noop_without_register_cli_command():
    class NoCliCtx:
        pass
    # Should not raise
    bort_cli.register_cli(NoCliCtx())


def test_setup_bort_cli_adds_three_subcommands():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    bort_parser = sub.add_parser("bort")
    bort_cli.setup_bort_cli(bort_parser)

    # Parse each sub-subcommand and confirm `func` is set
    for name, expected_fn in [
        ("init-operator", bort_cli._cmd_init_operator),
        ("init-policy",   bort_cli._cmd_init_policy),
        ("doctor",        bort_cli._cmd_doctor),
    ]:
        args = parser.parse_args(["bort", name])
        assert getattr(args, "func", None) is expected_fn


def test_handle_bort_cli_dispatches_to_func():
    called = {}

    def fake_func(args):
        called["yes"] = True

    ns = argparse.Namespace(func=fake_func)
    bort_cli.handle_bort_cli(ns)
    assert called.get("yes") is True


def test_handle_bort_cli_no_subcommand_prints_usage():
    ns = argparse.Namespace()  # no `func`
    buf = io.StringIO()
    with redirect_stdout(buf):
        bort_cli.handle_bort_cli(ns)
    assert "Usage: hermes bort" in buf.getvalue()


def test_init_operator_prints_address_and_key():
    ns = argparse.Namespace()
    buf = io.StringIO()
    with redirect_stdout(buf):
        bort_cli._cmd_init_operator(ns)
    out = buf.getvalue()
    assert "Address: 0x" in out
    # The printed private key should be a 0x-prefixed 64-hex string somewhere in the output
    assert "0x" in out
    assert "BORT_OPERATOR_PRIVATE_KEY" in out
    assert "Fund" in out


def test_init_policy_round_trip(tmp_path):
    policy_path = tmp_path / "bort-policy.yaml"
    ns = argparse.Namespace(path=str(policy_path), force=False)
    buf = io.StringIO()
    with redirect_stdout(buf):
        bort_cli._cmd_init_policy(ns)
    assert policy_path.exists()
    assert "Wrote default policy" in buf.getvalue()

    # Second run without --force should refuse
    buf2 = io.StringIO()
    with redirect_stdout(buf2):
        bort_cli._cmd_init_policy(argparse.Namespace(path=str(policy_path), force=False))
    assert "already exists" in buf2.getvalue()

    # With --force it overwrites
    buf3 = io.StringIO()
    with redirect_stdout(buf3):
        bort_cli._cmd_init_policy(argparse.Namespace(path=str(policy_path), force=True))
    assert "Wrote default policy" in buf3.getvalue()


@pytest.mark.asyncio
async def test_doctor_async_runs_without_crashing(monkeypatch):
    """doctor against live infra: should print a report without raising,
    regardless of whether env vars are set."""
    monkeypatch.delenv("BORT_OPERATOR_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("BORT_ALLOW_BROADCAST", raising=False)
    buf = io.StringIO()
    with redirect_stdout(buf):
        await bort_cli._doctor_async()
    out = buf.getvalue()
    assert "hermes-bort doctor" in out
    assert "BSC RPC" in out
    assert "Done." in out


# ----- evolve subcommand -----
def _evolve_ns(**over):
    base = dict(skill="myskill", token_id=11100, iterations=10, repo=None,
                priority=50, commit_only=False, only="both", min_improvement=0.0)
    base.update(over)
    return argparse.Namespace(**base)


def test_setup_bort_cli_wires_evolve():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    bort_parser = sub.add_parser("bort")
    bort_cli.setup_bort_cli(bort_parser)

    args = parser.parse_args(["bort", "evolve", "myskill", "--token-id", "11100"])
    assert args.func is bort_cli._cmd_evolve
    assert args.skill == "myskill"
    assert args.token_id == 11100
    assert args.iterations == 10
    assert args.priority == 50
    assert args.only == "both"
    assert args.min_improvement == 0.0
    assert args.commit_only is False
    assert args.repo is None


def test_cmd_evolve_invalid_skill():
    buf = io.StringIO()
    with redirect_stdout(buf):
        bort_cli._cmd_evolve(_evolve_ns(skill="bad name"))
    assert "Invalid skill name" in buf.getvalue()


def test_cmd_evolve_repo_not_found(monkeypatch):
    monkeypatch.setattr(evolution_loop, "locate_self_evolution_repo",
                        lambda explicit=None: None)
    buf = io.StringIO()
    with redirect_stdout(buf):
        bort_cli._cmd_evolve(_evolve_ns(repo="/no/such/dir"))
    assert "repo not found" in buf.getvalue()


def test_print_evolve_result_formatting():
    result = {
        "evolution": {"status": "ok", "pinned_cid": "Qm1", "tx_hash": "0xaaa"},
        "learning": {"status": "ok", "tx_hash": "0xbbb"},
        "chained": True,
        "content_hash": "0x" + "ab" * 32,
        "summary": "fully committed on-chain: knowledge source + learning event.",
    }
    buf = io.StringIO()
    with redirect_stdout(buf):
        bort_cli._print_evolve_result(result)
    out = buf.getvalue()
    assert "EVOLUTION" in out
    assert "linked hash" in out
    assert "fully committed" in out


def test_cmd_evolve_happy_path(tmp_path, monkeypatch):
    repo = tmp_path / "se"
    repo.mkdir()
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    monkeypatch.setattr(evolution_loop, "locate_self_evolution_repo",
                        lambda explicit=None: repo)
    monkeypatch.setattr(evolution_loop, "resolve_python", lambda r: "python")

    async def fake_preflight(token_id):
        return {"ok": True, "broadcast": False, "problems": [], "notes": ["dry run"]}

    def fake_run_optimizer(repo_, python, skill, iterations,
                           eval_source="synthetic", timeout=None):
        return evolution_loop.OptimizerRun(
            ran=True, returncode=0, run_dir=run_dir,
            metrics={"improvement": 0.2, "iterations": 4},
        )

    async def fake_chain(token_id, rd, *, priority=50, only="both"):
        return {
            "evolution": {"status": "ok"}, "learning": {"status": "ok"},
            "chained": True, "content_hash": "0x" + "12" * 32,
            "summary": "fully committed on-chain.",
        }

    monkeypatch.setattr(evolution_loop, "preflight_check", fake_preflight)
    monkeypatch.setattr(evolution_loop, "run_optimizer", fake_run_optimizer)
    monkeypatch.setattr(evolution_loop, "chain_commits", fake_chain)

    buf = io.StringIO()
    with redirect_stdout(buf):
        bort_cli._cmd_evolve(_evolve_ns())
    assert "fully committed" in buf.getvalue()
