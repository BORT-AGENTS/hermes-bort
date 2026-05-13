"""Async IPFS fetch with gateway fallback.

Per BORT's dual-gateway pattern: pinata for binary, ipfs.io for JSON. In practice both
work for JSON for most CIDs (verified for token 11100), so we just try in order and
fall back. Returns parsed JSON, raw bytes, or None on full failure.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx


PINATA_GATEWAY = "https://gateway.pinata.cloud/ipfs/"
IPFS_IO_GATEWAY = "https://ipfs.io/ipfs/"
W3S_GATEWAY = "https://w3s.link/ipfs/"

GATEWAYS = [PINATA_GATEWAY, IPFS_IO_GATEWAY, W3S_GATEWAY]

DEFAULT_TIMEOUT = 10.0

PINATA_PIN_FILE_URL = "https://api.pinata.cloud/pinning/pinFileToIPFS"
PINATA_PIN_JSON_URL = "https://api.pinata.cloud/pinning/pinJSONToIPFS"
PIN_TIMEOUT = 30.0


def _resolve(uri: str, gateway: str) -> str:
    if uri.startswith("ipfs://"):
        return gateway + uri[len("ipfs://"):]
    if uri.startswith("http://") or uri.startswith("https://"):
        return uri
    # bare CID
    return gateway + uri


async def fetch_json(uri: str, *, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any] | None:
    """Fetch and JSON-parse an ipfs:// (or http) URI. Returns None on full failure."""
    if not uri:
        return None
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for gw in GATEWAYS:
            url = _resolve(uri, gw)
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    try:
                        return r.json()
                    except json.JSONDecodeError:
                        continue
            except httpx.RequestError:
                continue
            # If this URI was already an http/https, no point retrying with gateways
            if uri.startswith("http"):
                break
    return None


async def fetch_bytes(uri: str, *, timeout: float = DEFAULT_TIMEOUT) -> bytes | None:
    """Fetch raw bytes (e.g. for GLB models). Returns None on failure."""
    if not uri:
        return None
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for gw in GATEWAYS:
            url = _resolve(uri, gw)
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    return r.content
            except httpx.RequestError:
                continue
            if uri.startswith("http"):
                break
    return None


# --- Pinata pinning (legacy key+secret auth) -------------------------------------
def _pinata_headers() -> dict[str, str] | None:
    """Return Pinata auth headers, or None if PINATA_API_KEY/PINATA_API_SECRET aren't set."""
    key = os.environ.get("PINATA_API_KEY", "").strip()
    secret = os.environ.get("PINATA_API_SECRET", "").strip()
    if not key or not secret:
        return None
    return {"pinata_api_key": key, "pinata_secret_api_key": secret}


def pinata_configured() -> bool:
    return _pinata_headers() is not None


async def pin_bytes(data: bytes, name: str, *, timeout: float = PIN_TIMEOUT) -> str | None:
    """Pin raw bytes to Pinata IPFS. Returns the CID (Qm... / bafy...), or None on
    missing creds / failure. Used to anchor session memory JSONL and evolved skill files."""
    headers = _pinata_headers()
    if headers is None:
        return None
    files = {"file": (name, data, "application/octet-stream")}
    form = {"pinataMetadata": json.dumps({"name": name})}
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            r = await client.post(PINATA_PIN_FILE_URL, headers=headers, files=files, data=form)
            if r.status_code == 200:
                return r.json().get("IpfsHash")
        except (httpx.RequestError, json.JSONDecodeError):
            return None
    return None


async def pin_json(obj: dict[str, Any], name: str, *, timeout: float = PIN_TIMEOUT) -> str | None:
    """Pin a JSON object to Pinata IPFS. Returns the CID, or None."""
    headers = _pinata_headers()
    if headers is None:
        return None
    body = {"pinataContent": obj, "pinataMetadata": {"name": name}}
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            r = await client.post(
                PINATA_PIN_JSON_URL, headers={**headers, "Content-Type": "application/json"}, json=body,
            )
            if r.status_code == 200:
                return r.json().get("IpfsHash")
        except (httpx.RequestError, json.JSONDecodeError):
            return None
    return None
