"""On-chain readers for BAP-578 contracts on BSC mainnet.

Owns:
- Deployed mainnet addresses (BSC chainId 56).
- Minimal ABI fragments: only functions we actually call.
- Lazy-init AsyncWeb3 from BSC_RPC_URL (default bsc-dataseed.binance.org).
- Per-contract async helpers returning plain Python dicts.

Logic-contract reads (Hunter Position, CTO Campaign) live in bort_logic_adapters.py.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from web3 import AsyncWeb3, AsyncHTTPProvider
from web3.contract.async_contract import AsyncContract


# ---- Deployed mainnet addresses (BSC chainId 56) ---------------------------------
ADDRESSES: dict[str, str] = {
    "BAP578":                 "0x15b15DF2fFFF6653C21C11b93fB8A7718CE854Ce",
    "KnowledgeRegistry":      "0xb8E808f7916a53c595a0740E656c8bF05388E29a",
    "MerkleTreeLearning":     "0x69dd4d2a0970751e1825d4291425641c1c2e6c81",
    # Live VPM v2 (UUPS proxy, deployed 2026-05-11). Bridges off-runtime callers to logic contracts
    # via forwardHandleAction. Has authorizedCaller on Hunter / Trading V5 / CTO.
    "VaultPermissionManager": "0x0fA3F984F7999d31C28055260637D1bCEA34919A",
    # Old VPM (raw impl, never proxified, locked by _disableInitializers).
    # Kept here as documentation only. Do NOT call.
    "VaultPermissionManagerV1Dead": "0x94afa41a02f32105b64be9199577748a9357dd6b",
    "CircuitBreaker":         "0x907c460b8a9d698e2db460d86ddafa5bb5f12b0a",
    "MintGate":               "0x97e8f3B4BFfC1982B2791B21609c3B2542c5eB50",
    "MarketplaceV3":          "0x73B35e03bBC4F0F59B47106F70Cd90f579D3497b",
    "HunterLogic":            "0x4F35D6B3DEdecfe3aD6600b39A705BcD53E2aE81",
    "TradingLogicV5":         "0x933f288e3213a0A05F28A4A6Ec5790129bdaE6d7",
    "CTOLogic":               "0x8E54612c12710c41ae57abAa8D4637f394DE2b0B",
}

# PermissionLevel enum values used by VaultPermissionManagerV2
PERMISSION_LEVEL = {"NONE": 0, "READ": 1, "WRITE": 2, "ADMIN": 3}

LOGIC_NAME_BY_ADDRESS: dict[str, str] = {
    ADDRESSES["HunterLogic"].lower():    "Hunter",
    ADDRESSES["TradingLogicV5"].lower(): "Trading V5",
    ADDRESSES["CTOLogic"].lower():       "CTO",
}

KNOWLEDGE_TYPES = ["BASE", "CONTEXT", "MEMORY", "INSTRUCTION", "REFERENCE", "DYNAMIC"]
STATUS_NAMES = ["Paused", "Active", "Terminated"]


class BortChainError(RuntimeError):
    """Raised when a BAP-578 chain read fails unrecoverably."""


# ---- Minimal ABIs ----------------------------------------------------------------
_KNOWLEDGE_SOURCE_TUPLE = [
    {"name": "id",            "type": "uint256"},
    {"name": "uri",           "type": "string"},
    {"name": "sourceType",    "type": "uint8"},
    {"name": "version",       "type": "uint256"},
    {"name": "priority",      "type": "uint256"},
    {"name": "active",        "type": "bool"},
    {"name": "addedAt",       "type": "uint256"},
    {"name": "lastUpdated",   "type": "uint256"},
    {"name": "description",   "type": "string"},
    {"name": "contentHash",   "type": "bytes32"},
]

BAP578_ABI: list[dict[str, Any]] = [
    {"inputs": [{"name": "tokenId", "type": "uint256"}], "name": "tokenURI",
     "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "tokenId", "type": "uint256"}], "name": "ownerOf",
     "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "tokenId", "type": "uint256"}], "name": "getState",
     "outputs": [{"components": [
         {"name": "balance",             "type": "uint256"},
         {"name": "status",              "type": "uint8"},
         {"name": "owner",               "type": "address"},
         {"name": "logicAddress",        "type": "address"},
         {"name": "lastActionTimestamp", "type": "uint256"},
     ], "type": "tuple"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "tokenId", "type": "uint256"}], "name": "getAgentMetadata",
     "outputs": [{"components": [
         {"name": "persona",      "type": "string"},
         {"name": "experience",   "type": "string"},
         {"name": "voiceHash",    "type": "string"},
         {"name": "animationURI", "type": "string"},
         {"name": "vaultURI",     "type": "string"},
         {"name": "vaultHash",    "type": "bytes32"},
     ], "type": "tuple"}], "stateMutability": "view", "type": "function"},
]

KNOWLEDGE_ABI: list[dict[str, Any]] = [
    {"inputs": [{"name": "tokenId", "type": "uint256"}], "name": "getKnowledgeSources",
     "outputs": [{"components": _KNOWLEDGE_SOURCE_TUPLE, "type": "tuple[]"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "tokenId", "type": "uint256"}], "name": "getActiveKnowledgeSources",
     "outputs": [{"components": _KNOWLEDGE_SOURCE_TUPLE, "type": "tuple[]"}],
     "stateMutability": "view", "type": "function"},
    # KnowledgeRegistryV2 additions
    {"inputs": [
        {"name": "tokenId",     "type": "uint256"},
        {"name": "vaultId",     "type": "string"},
        {"name": "uri",         "type": "string"},
        {"name": "sourceType",  "type": "uint8"},
        {"name": "priority",    "type": "uint256"},
        {"name": "description", "type": "string"},
        {"name": "contentHash", "type": "bytes32"},
     ], "name": "addKnowledgeSourceDelegated",
     "outputs": [{"type": "uint256"}], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [], "name": "vaultPermissionV2",
     "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
]

CIRCUIT_BREAKER_ABI: list[dict[str, Any]] = [
    {"inputs": [], "name": "globalPause",
     "outputs": [{"type": "bool"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "contractAddress", "type": "address"}], "name": "isContractPaused",
     "outputs": [{"type": "bool"}], "stateMutability": "view", "type": "function"},
]

VAULT_PERMISSION_ABI: list[dict[str, Any]] = [
    # V1 (inherited by V2): create + grant + revoke + check
    {"inputs": [
        {"name": "vaultId",     "type": "string"},
        {"name": "description", "type": "string"},
    ], "name": "createVault", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [
        {"name": "delegate", "type": "address"},
        {"name": "vaultId",  "type": "string"},
        {"name": "level",    "type": "uint8"},
        {"name": "duration", "type": "uint256"},
        {"name": "metadata", "type": "string"},
    ], "name": "grantPermission",
     "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [
        {"name": "vaultOwner",    "type": "address"},
        {"name": "vaultId",       "type": "string"},
        {"name": "accessor",      "type": "address"},
        {"name": "requiredLevel", "type": "uint8"},
    ], "name": "checkPermission",
     "outputs": [
        {"name": "hasPermission",   "type": "bool"},
        {"name": "permissionLevel", "type": "uint8"},
     ], "stateMutability": "view", "type": "function"},
    # V2 additions: the forwarder + its view pre-flight
    {"inputs": [
        {"name": "tokenId",       "type": "uint256"},
        {"name": "vaultId",       "type": "string"},
        {"name": "logicAddress",  "type": "address"},
        {"name": "action",        "type": "string"},
        {"name": "payload",       "type": "bytes"},
     ], "name": "forwardHandleAction",
     "outputs": [
        {"name": "success", "type": "bool"},
        {"name": "result",  "type": "bytes"},
     ], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [
        {"name": "tokenId",  "type": "uint256"},
        {"name": "vaultId",  "type": "string"},
        {"name": "accessor", "type": "address"},
     ], "name": "canForward",
     "outputs": [{"type": "bool"}], "stateMutability": "view", "type": "function"},
]


# ---- Web3 provider ---------------------------------------------------------------
def _rpc_url() -> str:
    return os.environ.get("BSC_RPC_URL", "https://bsc-dataseed.binance.org")


@lru_cache(maxsize=1)
def web3() -> AsyncWeb3:
    """Return the async Web3 instance. Lazily created, cached for the process lifetime."""
    return AsyncWeb3(AsyncHTTPProvider(_rpc_url()))


def reset_web3() -> None:
    """Drop the cached Web3 instance: useful in tests when env changes."""
    web3.cache_clear()


def _contract(address_key: str, abi: list[dict[str, Any]]) -> AsyncContract:
    addr = AsyncWeb3.to_checksum_address(ADDRESSES[address_key])
    return web3().eth.contract(address=addr, abi=abi)


def _logic_name(addr: str | None) -> str | None:
    if not addr:
        return None
    return LOGIC_NAME_BY_ADDRESS.get(addr.lower())


def _bytes32_to_hex(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    return str(value)


# ---- Read helpers (BAP578) -------------------------------------------------------
async def get_token_uri(token_id: int) -> str:
    contract = _contract("BAP578", BAP578_ABI)
    return await contract.functions.tokenURI(token_id).call()


async def get_owner(token_id: int) -> str:
    contract = _contract("BAP578", BAP578_ABI)
    return await contract.functions.ownerOf(token_id).call()


async def get_state(token_id: int) -> dict[str, Any]:
    contract = _contract("BAP578", BAP578_ABI)
    raw = await contract.functions.getState(token_id).call()
    balance, status, owner, logic_addr, last_action = raw
    return {
        "balance_wei":            int(balance),
        "balance_bnb":            float(AsyncWeb3.from_wei(int(balance), "ether")),
        "status":                 STATUS_NAMES[status] if 0 <= status < len(STATUS_NAMES) else str(status),
        "status_id":              int(status),
        "owner":                  owner,
        "logic_address":          logic_addr,
        "logic_name":             _logic_name(logic_addr),
        "last_action_timestamp":  int(last_action),
    }


async def get_agent_metadata(token_id: int) -> dict[str, Any]:
    contract = _contract("BAP578", BAP578_ABI)
    raw = await contract.functions.getAgentMetadata(token_id).call()
    persona, experience, voice_hash, animation_uri, vault_uri, vault_hash = raw
    return {
        "persona":       persona or "",
        "experience":    experience or "",
        "voice_hash":    voice_hash or "",
        "animation_uri": animation_uri or "",
        "vault_uri":     vault_uri or "",
        "vault_hash":    _bytes32_to_hex(vault_hash),
    }


# ---- Read helpers (KnowledgeRegistry) --------------------------------------------
def _decode_knowledge_source(raw: tuple) -> dict[str, Any]:
    (sid, uri, source_type, version, priority, active,
     added_at, last_updated, description, content_hash) = raw
    return {
        "id":             int(sid),
        "uri":            uri,
        "source_type":    KNOWLEDGE_TYPES[source_type] if 0 <= source_type < len(KNOWLEDGE_TYPES) else str(source_type),
        "source_type_id": int(source_type),
        "version":        int(version),
        "priority":       int(priority),
        "active":         bool(active),
        "added_at":       int(added_at),
        "last_updated":   int(last_updated),
        "description":    description or "",
        "content_hash":   _bytes32_to_hex(content_hash),
    }


async def get_knowledge_sources(token_id: int, active_only: bool = True) -> list[dict[str, Any]]:
    contract = _contract("KnowledgeRegistry", KNOWLEDGE_ABI)
    func = contract.functions.getActiveKnowledgeSources if active_only else contract.functions.getKnowledgeSources
    raw_list = await func(token_id).call()
    return [_decode_knowledge_source(r) for r in raw_list]


# ---- Read helpers (CircuitBreaker) -----------------------------------------------
async def get_global_pause() -> bool:
    contract = _contract("CircuitBreaker", CIRCUIT_BREAKER_ABI)
    return bool(await contract.functions.globalPause().call())


async def is_contract_paused(target_address: str) -> bool:
    contract = _contract("CircuitBreaker", CIRCUIT_BREAKER_ABI)
    addr = AsyncWeb3.to_checksum_address(target_address)
    return bool(await contract.functions.isContractPaused(addr).call())


# ---- Read helpers (VaultPermissionManagerV2) -------------------------------------
async def check_permission(owner: str, vault_id: str, accessor: str, level: int = 2) -> bool:
    """Returns True iff `accessor` has permission level >= `level` on (owner, vault_id).

    Note: vault_id is a STRING in the deployed contract (not bytes32 as earlier plugin
    code assumed). Default `level=2` is WRITE: enough for forwardHandleAction.
    """
    contract = _contract("VaultPermissionManager", VAULT_PERMISSION_ABI)
    o = AsyncWeb3.to_checksum_address(owner)
    a = AsyncWeb3.to_checksum_address(accessor)
    has, _ = await contract.functions.checkPermission(o, vault_id, a, int(level)).call()
    return bool(has)


async def can_forward(token_id: int, vault_id: str, accessor: str) -> bool:
    """VPM v2's cheap pre-flight check. Returns True iff `accessor` can call
    forwardHandleAction for the agent currently owning `token_id` under `vault_id`."""
    contract = _contract("VaultPermissionManager", VAULT_PERMISSION_ABI)
    a = AsyncWeb3.to_checksum_address(accessor)
    return bool(await contract.functions.canForward(int(token_id), vault_id, a).call())
