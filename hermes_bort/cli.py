"""CLI subcommands for hermes-bort.

Registers `hermes bort <subcommand>`:
  hermes bort init-operator  : generate an operator signing key
  hermes bort init-policy    : write the default bort-policy.yaml
  hermes bort doctor         : diagnose the setup (env vars, RPC, funding, permissions)

Wired into Hermes via ctx.register_cli_command in __init__.register().
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any


# init-operator
def _cmd_init_operator(args) -> None:
    from eth_account import Account

    acct = Account.create()
    key_hex = acct.key.hex()
    if not key_hex.startswith("0x"):
        key_hex = "0x" + key_hex
    addr = acct.address

    print()
    print("Generated a new BORT operator key.")
    print(f"  Address: {addr}")
    print()
    print("  Private key (set this in your environment: never share, screenshot, or commit it):")
    print(f"    {key_hex}")
    print()
    print("Next steps:")
    print(f"  1. Fund {addr} with ~0.01 BNB for gas.")
    print(f"  2. Set the env var before starting Hermes:")
    print(f"       export BORT_OPERATOR_PRIVATE_KEY={key_hex}        (bash)")
    print(f"       $env:BORT_OPERATOR_PRIVATE_KEY = '{key_hex}'      (PowerShell)")
    print(f"  3. Set BORT_ALLOW_BROADCAST=1 when you want writes enabled.")
    print(f"  4. Have each NFT owner run the bort_grant_permission_uri tool and sign the two txs")
    print(f"     (createVault + grantPermission) so this operator can act on their agents.")
    print()


# init-policy
def _cmd_init_policy(args) -> None:
    from .bort_policy import write_default_policy, DEFAULT_POLICY_PATH

    target = getattr(args, "path", None) or os.environ.get("BORT_POLICY_PATH") or DEFAULT_POLICY_PATH
    expanded = Path(os.path.expanduser(target))
    if expanded.exists() and not getattr(args, "force", False):
        print(f"Policy already exists at {expanded}.")
        print("Pass --force to overwrite, or edit it directly.")
        return
    written = write_default_policy(str(expanded))
    print(f"Wrote default policy to {written}")
    print("Edit per_action dispositions (auto / confirm / block) and per_action_max_bnb caps as needed.")


# doctor
def _ok(label: str, detail: str = "") -> None:
    print(f"  [ OK ] {label}" + (f": {detail}" if detail else ""))


def _warn(label: str, detail: str = "") -> None:
    print(f"  [WARN] {label}" + (f": {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"  [FAIL] {label}" + (f": {detail}" if detail else ""))


async def _doctor_async() -> None:
    from . import bort_chain, bort_signer, bort_api, bort_policy
    from web3 import AsyncWeb3

    print("hermes-bort doctor")
    print("=" * 60)

    # --- env: RPC ---
    rpc = os.environ.get("BSC_RPC_URL", "https://bsc-dataseed.binance.org")
    try:
        block = await bort_chain.web3().eth.block_number
        _ok("BSC RPC reachable", f"{rpc} (block {block})")
    except Exception as e:  # noqa: BLE001
        _fail("BSC RPC", f"{rpc}: {type(e).__name__}: {e}")

    # --- env: runtime API ---
    api_url = os.environ.get("BORT_API_URL", "https://bap578-nfa-platform.onrender.com")
    try:
        stats = await bort_api.client().get_leaderboard_stats()
        _ok("BORT runtime API reachable", api_url if stats is not None else f"{api_url} (no stats payload)")
    except Exception as e:  # noqa: BLE001
        _warn("BORT runtime API", f"{api_url}: {type(e).__name__}: {e}")

    # --- operator key ---
    try:
        operator = bort_signer.operator_address()
        _ok("BORT_OPERATOR_PRIVATE_KEY set", f"operator {operator}")
        try:
            bal = await bort_signer.operator_balance_bnb()
            if bal >= 0.005:
                _ok("Operator funded", f"{bal} BNB")
            elif bal > 0:
                _warn("Operator low on gas", f"{bal} BNB: top up to ~0.01 BNB")
            else:
                _warn("Operator has 0 BNB", "fund it before broadcasting writes")
        except Exception as e:  # noqa: BLE001
            _warn("Could not read operator balance", f"{type(e).__name__}: {e}")
    except bort_signer.OperatorKeyMissing:
        _warn("BORT_OPERATOR_PRIVATE_KEY not set", "writes disabled until you set it (run `hermes bort init-operator`)")
    except Exception as e:  # noqa: BLE001
        _fail("BORT_OPERATOR_PRIVATE_KEY invalid", f"{type(e).__name__}: {e}")

    # --- broadcast flag ---
    if os.environ.get("BORT_ALLOW_BROADCAST", "").strip().lower() in ("1", "true", "yes", "on"):
        _ok("BORT_ALLOW_BROADCAST set", "real on-chain writes enabled")
    else:
        _warn("BORT_ALLOW_BROADCAST not set", "writes will simulate only; set to 1 to enable broadcasts")

    # --- policy file ---
    policy_path = Path(os.path.expanduser(
        os.environ.get("BORT_POLICY_PATH") or bort_policy.DEFAULT_POLICY_PATH
    ))
    if policy_path.exists():
        try:
            pol = bort_policy.BortPolicy.load()
            _ok("Policy file present", f"{policy_path} (mode={pol.mode})")
        except Exception as e:  # noqa: BLE001
            _warn("Policy file unreadable", f"{policy_path}: {type(e).__name__}: {e}: using defaults")
    else:
        _warn("No policy file", f"{policy_path} missing: built-in defaults in use. Run `hermes bort init-policy`.")

    # --- pinata creds (Phase 2 memory anchor) ---
    if os.environ.get("PINATA_API_KEY") and os.environ.get("PINATA_API_SECRET"):
        _ok("Pinata credentials set", "(used for IPFS memory anchor: Phase 2)")
    else:
        _warn("Pinata credentials not set", "fine for now; needed only for on-chain memory anchoring later")

    # --- VPM v2 sanity ---
    vpm = bort_chain.ADDRESSES["VaultPermissionManager"]
    try:
        # canForward against a dummy accessor just to confirm the contract responds
        _ = await bort_chain.can_forward(1, "1", "0x0000000000000000000000000000000000000001")
        _ok("VPM v2 reachable", vpm)
    except Exception as e:  # noqa: BLE001
        _fail("VPM v2 read failed", f"{vpm}: {type(e).__name__}: {e}")

    print("=" * 60)
    print("Done. [FAIL] items block writes; [WARN] items are advisory.")


def _cmd_doctor(args) -> None:
    asyncio.run(_doctor_async())


# anchor-memory / commit-evolution (CLI aliases for the same-named tools)
def _print_kr_result(parsed: dict) -> None:
    """Shared output formatting for the KR-v2 delegated-write tools."""
    import json as _json
    if "error" in parsed:
        print(f"Error: {parsed['error']}")
        return
    print(f"status:       {parsed.get('status')}")
    if parsed.get("pinned_cid"):
        print(f"pinned CID:   ipfs://{parsed['pinned_cid']}")
    if parsed.get("description"):
        print(f"description:  {parsed['description']}")
    if parsed.get("tx_hash"):
        print(f"tx hash:      {parsed['tx_hash']}")
        print(f"bscscan:      https://bscscan.com/tx/{parsed['tx_hash']}")
        print(f"gas used:     {parsed.get('gas_used')}")
    if parsed.get("reason"):
        print(f"reason:       {parsed['reason']}")
    pf = parsed.get("preflight")
    if pf:
        print(f"preflight:    {_json.dumps(pf, default=str)}")


def _cmd_anchor_memory(args) -> None:
    import json as _json
    from .tools.anchor_memory import handle as anchor_handle
    raw = asyncio.run(anchor_handle({"token_id": args.token_id, "priority": args.priority}))
    _print_kr_result(_json.loads(raw))


def _cmd_commit_evolution(args) -> None:
    import json as _json
    from .tools.commit_evolution import handle as commit_handle
    raw = asyncio.run(commit_handle({
        "token_id": args.token_id, "output_dir": args.output_dir, "priority": args.priority,
    }))
    _print_kr_result(_json.loads(raw))


# argparse wiring
def setup_bort_cli(subparser) -> None:
    """Hermes calls this with the `bort` subparser. We add sub-subcommands."""
    sub = subparser.add_subparsers(dest="bort_command")

    p_op = sub.add_parser("init-operator", help="Generate a new operator signing key")
    p_op.set_defaults(func=_cmd_init_operator)

    p_pol = sub.add_parser("init-policy", help="Write the default ~/.hermes/bort-policy.yaml")
    p_pol.add_argument("--path", help="Override the policy file path")
    p_pol.add_argument("--force", action="store_true", help="Overwrite if it already exists")
    p_pol.set_defaults(func=_cmd_init_policy)

    p_doc = sub.add_parser("doctor", help="Diagnose hermes-bort setup (env, RPC, funding, permissions)")
    p_doc.set_defaults(func=_cmd_doctor)

    p_mem = sub.add_parser("anchor-memory", help="Pin local session memory to IPFS and anchor it on KR v2 (MEMORY source)")
    p_mem.add_argument("--token-id", dest="token_id", type=int, required=True, help="BAP-578 token ID")
    p_mem.add_argument("--priority", type=int, default=10, help="Knowledge-source priority (default 10)")
    p_mem.set_defaults(func=_cmd_anchor_memory)

    p_evo = sub.add_parser("commit-evolution", help="Anchor a self-evolution result on KR v2 (INSTRUCTION source)")
    p_evo.add_argument("output_dir", help="Path to the self-evolution output dir (output/<skill>/<ts>/)")
    p_evo.add_argument("--token-id", dest="token_id", type=int, required=True, help="BAP-578 token ID to anchor to")
    p_evo.add_argument("--priority", type=int, default=50, help="Knowledge-source priority (default 50)")
    p_evo.set_defaults(func=_cmd_commit_evolution)


def handle_bort_cli(args) -> None:
    """Dispatch to the chosen sub-subcommand."""
    func = getattr(args, "func", None)
    if func is None:
        print("Usage: hermes bort <init-operator | init-policy | doctor>")
        return
    func(args)


def register_cli(ctx) -> None:
    """Register the `bort` CLI command. No-op-safe if ctx lacks register_cli_command."""
    if not hasattr(ctx, "register_cli_command"):
        return
    ctx.register_cli_command(
        name="bort",
        help="hermes-bort: operator key, policy, and setup diagnostics",
        setup_fn=setup_bort_cli,
        handler_fn=handle_bort_cli,
        description="Manage the hermes-bort operator key and policy, and run setup diagnostics.",
    )
