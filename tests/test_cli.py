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
