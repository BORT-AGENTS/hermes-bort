"""Tests for the Pinata pin helpers in hermes_bort.bort_ipfs.

No network calls: without PINATA_API_KEY/PINATA_API_SECRET the pin functions
short-circuit to None and pinata_configured() returns False.
"""
from __future__ import annotations

import pytest

from hermes_bort import bort_ipfs


def test_pinata_not_configured_without_creds(monkeypatch):
    monkeypatch.delenv("PINATA_API_KEY", raising=False)
    monkeypatch.delenv("PINATA_API_SECRET", raising=False)
    assert bort_ipfs.pinata_configured() is False


def test_pinata_configured_with_creds(monkeypatch):
    monkeypatch.setenv("PINATA_API_KEY", "k")
    monkeypatch.setenv("PINATA_API_SECRET", "s")
    assert bort_ipfs.pinata_configured() is True


@pytest.mark.asyncio
async def test_pin_bytes_returns_none_without_creds(monkeypatch):
    monkeypatch.delenv("PINATA_API_KEY", raising=False)
    monkeypatch.delenv("PINATA_API_SECRET", raising=False)
    assert await bort_ipfs.pin_bytes(b"hello", "test.txt") is None


@pytest.mark.asyncio
async def test_pin_json_returns_none_without_creds(monkeypatch):
    monkeypatch.delenv("PINATA_API_KEY", raising=False)
    monkeypatch.delenv("PINATA_API_SECRET", raising=False)
    assert await bort_ipfs.pin_json({"a": 1}, "test.json") is None
