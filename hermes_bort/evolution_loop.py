"""Self-evolution to on-chain learning loop.

Support module for `hermes bort evolve`. Runs the NousResearch
hermes-agent-self-evolution optimizer as a subprocess, detects whether a skill
improved, and chains the two on-chain commits (commit_evolution + commit_learning)
linked by one content hash.

The two on-chain effects (verified against the logic contracts):
  - commit_evolution -> KnowledgeRegistryV2.addKnowledgeSourceDelegated writes a
    persistent INSTRUCTION knowledge source (the evolved skill's IPFS CID).
  - commit_learning  -> record_learning emits a permanent LearningRecorded event
    (tokenId, dataHash, interactionCount, timestamp). It mutates no score; the
    event itself is the verifiable on-chain learning record.

The same content hash (keccak of evolved_skill.md) ties the two together.

Plain support code: no Hermes tool/CLI registration here. The CLI wiring is in cli.py.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eth_utils import keccak


ENV_REPO = "BORT_SELF_EVOLUTION_REPO"
ENV_PYTHON = "BORT_SELF_EVOLUTION_PYTHON"

_SKILL_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_OPTIMIZER_ENTRY = Path("evolution") / "skills" / "evolve_skill.py"


def valid_skill_name(skill: str) -> bool:
    """Skill names must be path-safe: letters, digits, dot, dash, underscore."""
    return bool(skill) and bool(_SKILL_RE.match(skill))


def locate_self_evolution_repo(explicit: str | None = None) -> Path | None:
    """Find the hermes-agent-self-evolution repo.

    Order: explicit (--repo) -> $BORT_SELF_EVOLUTION_REPO ->
    ~/.hermes/hermes-agent-self-evolution -> sibling of the hermes-bort repo root.
    Returns the first path that exists and contains evolution/skills/evolve_skill.py.

    The sibling fallback only works for an editable install of hermes-bort; a
    pip-installed hermes-bort must set $BORT_SELF_EVOLUTION_REPO.
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(os.path.expanduser(explicit)))
    env = os.environ.get(ENV_REPO, "").strip()
    if env:
        candidates.append(Path(os.path.expanduser(env)))
    candidates.append(Path(os.path.expanduser("~/.hermes/hermes-agent-self-evolution")))
    # <repo_root>/hermes_bort/evolution_loop.py -> repo_root.parent is the siblings dir
    repo_root = Path(__file__).resolve().parents[1]
    candidates.append(repo_root.parent / "hermes-agent-self-evolution")

    for c in candidates:
        try:
            if (c / _OPTIMIZER_ENTRY).is_file():
                return c
        except OSError:
            continue
    return None


def resolve_python(repo: Path) -> str:
    """Pick the interpreter to run the optimizer.

    Never hermes-bort's sys.executable: the self-evolution repo needs its own
    DSPy/GEPA deps. Order: $BORT_SELF_EVOLUTION_PYTHON -> repo/.venv -> python on PATH.
    """
    env = os.environ.get(ENV_PYTHON, "").strip()
    if env:
        return env
    for rel in (Path(".venv") / "Scripts" / "python.exe", Path(".venv") / "bin" / "python"):
        cand = repo / rel
        if cand.is_file():
            return str(cand)
    return shutil.which("python") or "python"


def _skill_output_dir(repo: Path, skill: str) -> Path:
    return repo / "output" / skill


def snapshot_run_dirs(repo: Path, skill: str) -> set[str]:
    """Names of timestamped run dirs under output/<skill>/ (ignores evolved_FAILED.md)."""
    base = _skill_output_dir(repo, skill)
    if not base.is_dir():
        return set()
    return {p.name for p in base.iterdir() if p.is_dir()}


def newest_new_run_dir(repo: Path, skill: str, before: set[str]) -> tuple[Path | None, str]:
    """Resolve which run dir appeared since `before`. Returns (dir | None, note)."""
    base = _skill_output_dir(repo, skill)
    new = sorted(snapshot_run_dirs(repo, skill) - before)
    if not new:
        return None, "no new run dir"
    if len(new) == 1:
        return base / new[0], ""
    return base / new[-1], f"multiple new run dirs; picked {new[-1]}"


def latest_existing_run_dir(repo: Path, skill: str) -> Path | None:
    """Lexicographically-greatest run dir (timestamp format sorts chronologically)."""
    dirs = sorted(snapshot_run_dirs(repo, skill))
    if not dirs:
        return None
    return _skill_output_dir(repo, skill) / dirs[-1]


def has_failed_marker(repo: Path, skill: str) -> bool:
    """True if the optimizer left an evolved_FAILED.md (guardrails failed)."""
    return (_skill_output_dir(repo, skill) / "evolved_FAILED.md").is_file()


def evolved_skill_hash(run_dir: Path) -> str:
    """0x + keccak(evolved_skill.md bytes) — the same hash commit_evolution computes."""
    data = (Path(run_dir) / "evolved_skill.md").read_bytes()
    return "0x" + keccak(data).hex()


def build_optimizer_cmd(
    python: str,
    skill: str,
    iterations: int,
    eval_source: str = "synthetic",
    extra: list[str] | None = None,
) -> list[str]:
    """argv for `python -m evolution.skills.evolve_skill ...` (no shell)."""
    cmd = [
        python, "-m", "evolution.skills.evolve_skill",
        "--skill", skill,
        "--iterations", str(int(iterations)),
        "--eval-source", eval_source,
    ]
    if extra:
        cmd.extend(extra)
    return cmd


@dataclass
class OptimizerRun:
    ran: bool
    returncode: int | None = None
    run_dir: Path | None = None
    failed_marker: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    error: str | None = None


def run_optimizer(
    repo: Path,
    python: str,
    skill: str,
    iterations: int,
    eval_source: str = "synthetic",
    *,
    optimizer_model: str | None = None,
    eval_model: str | None = None,
    timeout: int | None = None,
) -> OptimizerRun:
    """Run the optimizer subprocess and resolve its result from the filesystem.

    Does not capture stdout/stderr: the optimizer streams rich progress for minutes.
    The optimizer exits 0 even on guardrail failure and no-improvement, so the
    outcome is decided by what appears on disk, not by the exit code alone.

    `optimizer_model` / `eval_model` are litellm model strings (e.g.
    `anthropic/claude-...`, `deepseek/...`); when None the optimizer's own
    defaults apply. The provider's API key is read from the inherited env.
    """
    before = snapshot_run_dirs(repo, skill)
    extra: list[str] = []
    if optimizer_model:
        extra += ["--optimizer-model", optimizer_model]
    if eval_model:
        extra += ["--eval-model", eval_model]
    cmd = build_optimizer_cmd(python, skill, iterations, eval_source, extra=extra or None)
    # Force UTF-8 in the child: the optimizer prints emoji via rich, which crashes
    # on a non-UTF-8 Windows console (cp1252 / cp1254 / ...).
    child_env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.run(cmd, cwd=str(repo), timeout=timeout, env=child_env)  # noqa: S603
    except FileNotFoundError as e:
        return OptimizerRun(ran=False, error=f"interpreter not found ({python}): {e}")
    except subprocess.TimeoutExpired:
        return OptimizerRun(ran=True, error=f"optimizer timed out after {timeout}s")
    except KeyboardInterrupt:
        return OptimizerRun(ran=True, error="optimizer interrupted (Ctrl-C)")

    run_dir, note = newest_new_run_dir(repo, skill, before)
    run = OptimizerRun(ran=True, returncode=proc.returncode, run_dir=run_dir, note=note)
    if proc.returncode != 0:
        run.error = f"optimizer exited with code {proc.returncode}"
    if run_dir is None:
        run.failed_marker = has_failed_marker(repo, skill)
        return run

    metrics_path = run_dir / "metrics.json"
    if metrics_path.is_file():
        try:
            loaded = json.loads(metrics_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                run.metrics = loaded
        except (OSError, json.JSONDecodeError):
            pass
    return run


async def preflight_check(token_id: int, only: str = "both") -> dict[str, Any]:
    """Verify on-chain prerequisites BEFORE the expensive optimizer run.

    Returns {ok, broadcast, problems: [...], notes: [...]}.

    Pinata is required whenever commit_evolution runs (only in {both, evolution}):
    it pins the evolved skill to IPFS even in dry-run mode. With `only="learning"`
    no content is pinned, so Pinata is not needed. When BORT_ALLOW_BROADCAST is on,
    the operator key, funding, and the vault grant are required too.
    """
    from . import bort_chain, bort_ipfs, bort_signer

    problems: list[str] = []
    notes: list[str] = []

    broadcast = os.environ.get("BORT_ALLOW_BROADCAST", "").strip().lower() in (
        "1", "true", "yes", "on",
    )

    if only == "learning":
        notes.append("learning-only: skipping IPFS/Pinata check (no content is pinned).")
    elif not bort_ipfs.pinata_configured():
        problems.append(
            "PINATA_API_KEY / PINATA_API_SECRET not set. commit_evolution pins the "
            "evolved skill to IPFS even in dry-run mode, so this is required. "
            "(Or use --only learning to record the on-chain learning event without IPFS.)"
        )

    if not broadcast:
        notes.append(
            "BORT_ALLOW_BROADCAST not set: dry run, both on-chain writes will simulate only."
        )
        return {"ok": not problems, "broadcast": False, "problems": problems, "notes": notes}

    operator: str | None = None
    try:
        operator = bort_signer.operator_address()
        notes.append(f"operator {operator}")
    except bort_signer.OperatorKeyMissing:
        problems.append(
            "BORT_ALLOW_BROADCAST=1 but BORT_OPERATOR_PRIVATE_KEY is not set. "
            "Run `hermes bort init-operator`."
        )
    except Exception as e:  # noqa: BLE001
        problems.append(f"operator key invalid: {type(e).__name__}: {e}")

    if operator is not None:
        try:
            bal = await bort_signer.operator_balance_bnb()
            if bal <= 0:
                problems.append("operator has 0 BNB: fund it before broadcasting.")
            elif bal < 0.005:
                notes.append(f"operator low on gas ({bal} BNB); top up to ~0.01 BNB.")
        except Exception as e:  # noqa: BLE001
            notes.append(f"could not read operator balance: {type(e).__name__}: {e}")

        try:
            can = await bort_chain.can_forward(token_id, str(token_id), operator)
            if can is not True:
                problems.append(
                    f"operator has no WRITE permission for agent {token_id}. Run "
                    "bort_grant_permission_uri and have the NFT owner sign the two txs "
                    "(createVault + grantPermission)."
                )
        except Exception as e:  # noqa: BLE001
            problems.append(f"could not check vault permission: {type(e).__name__}: {e}")

    return {"ok": not problems, "broadcast": True, "problems": problems, "notes": notes}


def _status_of(d: dict | None) -> str:
    if not d:
        return "skipped"
    if "error" in d:
        return "error"
    return str(d.get("status") or "unknown")


def _summary(evolution: dict | None, learning: dict | None, only: str) -> str:
    evo_st = _status_of(evolution)
    learn_st = _status_of(learning)
    if only == "evolution":
        if evo_st == "ok":
            return "evolution anchored on-chain (learning step not requested)."
        if evo_st == "simulated_only":
            return ("evolution dry run (learning step not requested). "
                    "Set BORT_ALLOW_BROADCAST=1 to write on-chain.")
        return f"evolution step: {evo_st}."
    if only == "learning":
        if learn_st == "ok":
            return "learning event recorded on-chain."
        if learn_st == "simulated_only":
            return "learning dry run. Set BORT_ALLOW_BROADCAST=1 to write on-chain."
        return f"learning step: {learn_st}."
    # both
    if evo_st == "ok" and learn_st == "ok":
        return "fully committed on-chain: knowledge source + learning event."
    if evo_st == "simulated_only" and learn_st == "simulated_only":
        return "dry run: nothing broadcast. Set BORT_ALLOW_BROADCAST=1 to write on-chain."
    if evo_st in ("ok", "simulated_only") and learn_st not in ("ok", "simulated_only"):
        return ("PARTIAL: knowledge source anchored, learning event failed. Retry: "
                "hermes bort evolve <skill> --token-id <id> --commit-only --only learning")
    return f"commit_evolution={evo_st}, commit_learning={learn_st}."


def _read_iterations(run_dir: Path) -> int:
    """GEPA round count from metrics.json. Event metadata only; defaults to 1."""
    metrics_path = Path(run_dir) / "metrics.json"
    if metrics_path.is_file():
        try:
            m = json.loads(metrics_path.read_text(encoding="utf-8"))
            if isinstance(m, dict):
                return int(m.get("iterations", 1)) or 1
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return 1


async def chain_commits(
    token_id: int,
    run_dir: Path,
    *,
    priority: int = 50,
    only: str = "both",
) -> dict[str, Any]:
    """Chain commit_evolution + commit_learning, linked by one content hash.

    `only`: "both" | "evolution" | "learning".
    - both      : commit_evolution, then commit_learning with its content_hash.
    - evolution : commit_evolution only.
    - learning  : commit_learning only; the hash is recomputed from evolved_skill.md.

    Returns {evolution, learning, chained, content_hash, summary}.
    """
    from .tools.commit_evolution import handle as commit_evolution
    from .tools.commit_learning import handle as commit_learning

    result: dict[str, Any] = {
        "evolution": None, "learning": None, "chained": False,
        "content_hash": None, "summary": "",
    }
    content_hash: str | None = None

    if only in ("both", "evolution"):
        evo = json.loads(await commit_evolution({
            "token_id": token_id, "output_dir": str(run_dir), "priority": priority,
        }))
        result["evolution"] = evo
        if "error" in evo or evo.get("status") not in ("ok", "simulated_only"):
            result["summary"] = "commit_evolution did not succeed; learning step skipped."
            return result
        content_hash = evo.get("content_hash")
        if only == "evolution":
            result["content_hash"] = content_hash
            result["summary"] = _summary(evo, None, "evolution")
            return result
    else:  # only == "learning"
        try:
            content_hash = evolved_skill_hash(run_dir)
        except OSError as e:
            result["summary"] = f"could not hash evolved_skill.md: {type(e).__name__}: {e}"
            return result

    if not content_hash:
        result["summary"] = "no content hash available; cannot record learning."
        return result
    result["content_hash"] = content_hash

    learn = json.loads(await commit_learning({
        "token_id": token_id,
        "data_hash": content_hash,
        "interaction_count": _read_iterations(run_dir),
    }))
    result["learning"] = learn
    result["chained"] = only == "both"
    result["summary"] = _summary(result["evolution"], learn, only)
    return result
