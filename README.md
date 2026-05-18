# hermes-bort

[![PyPI](https://img.shields.io/pypi/v/hermes-bort.svg)](https://pypi.org/project/hermes-bort/)
[![Python](https://img.shields.io/pypi/pyversions/hermes-bort.svg)](https://pypi.org/project/hermes-bort/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent) that lets a Hermes process read, act for, and anchor memory on BORT (BAP-578) agent NFTs on BSC mainnet.

A BORT agent is an ERC-721 with on-chain state, an IPFS-pinned identity, a logic
contract that exposes a small action catalog, and a knowledge registry. This plugin
gives Hermes:

- One-call multi-source reads of an agent's full state.
- Owner-signed deep links into the BORT dapp for marketplace flows.
- Operator-signed writes through `VaultPermissionManagerV2.forwardHandleAction`,
  gated by a local policy file and the Hermes approval prompt.
- Portable session memory: write to local JSONL during a chat, anchor to IPFS and
  `KnowledgeRegistryV2.addKnowledgeSourceDelegated` at session end, prefetch
  those sources at the next session's start.

## Install

```bash
pip install hermes-bort
```

Or install it straight from Hermes:

```bash
hermes plugins install BORT-AGENTS/hermes-bort
```

Either way, enable it in `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - hermes-bort
```

### From source

```bash
git clone https://github.com/BORT-AGENTS/hermes-bort
cd hermes-bort
pip install -e .[dev]
```

You can also drop the `hermes_bort/` package into `~/.hermes/plugins/` directly.

## Read-only quickstart

No setup beyond install. Reads hit the public BORT runtime API + BSC public RPC.

```python
from hermes_bort.tools.read_agent import handle as read_agent
import asyncio, json

raw = asyncio.run(read_agent({"token_id": 11100}))
print(json.dumps(json.loads(raw), indent=2)[:800])
```

Inside Hermes the same call surfaces as a tool: `bort_read_agent(token_id=11100)`.

## Configure for writes

Writes need three things: an operator key, a vault grant from the agent owner, and
permission from the local policy file.

### 1. Generate an operator key

```bash
hermes bort init-operator
```

Prints a fresh address + private key. Set `BORT_OPERATOR_PRIVATE_KEY` in your env
and fund the address with ~0.01 BNB for gas. The key is never written to disk by
the plugin and never leaves your process.

### 2. Write the default policy

```bash
hermes bort init-policy
```

Writes `~/.hermes/bort-policy.yaml`. Each action has a disposition (`auto` /
`confirm` / `block`) and an optional `per_action_max_bnb` cap. Defaults are
conservative: exits auto, trades and campaign lifecycle confirm, governance and
large transfers block.

### 3. Have the agent owner grant the operator

```python
from hermes_bort.tools.grant_permission import handle as grant
raw = await grant({"token_id": 11100, "operator": "0xYourOperatorAddress"})
```

Returns the two-step calldata (`createVault` then `grantPermission`) the owner
clicks through in their own wallet. Operator scope: `WRITE`, time-bound, limited
to the actions you whitelist in the grant description.

### 4. Run

By default writes simulate only. Set `BORT_ALLOW_BROADCAST=1` to enable real
broadcasting once you have confirmed simulation looks right.

```bash
export BORT_ALLOW_BROADCAST=1
```

### Doctor

```bash
hermes bort doctor
```

Walks the setup: RPC reachable, operator key valid and funded, policy file present
and parseable, Pinata creds set if you plan to anchor memory, and the
`hermes-agent-self-evolution` repo if you plan to use `evolve`.

### Evolve

Run the [`hermes-agent-self-evolution`](https://github.com/NousResearch/hermes-agent-self-evolution)
optimizer for a skill and, if it improved, commit the result on-chain in one step:

```bash
hermes bort evolve github-code-review --token-id 11100
```

It runs the optimizer as a subprocess and, only if `metrics.json` shows a real
improvement, chains two on-chain writes linked by one content hash:

- `commit_evolution` pins the evolved skill to IPFS and writes it as an
  `INSTRUCTION` knowledge source on `KnowledgeRegistryV2`.
- `commit_learning` emits a `LearningRecorded` event on the agent's logic
  contract: a permanent, timestamped, verifiable on-chain learning record.

The self-evolution repo is located via `--repo`, `$BORT_SELF_EVOLUTION_REPO`,
`~/.hermes/hermes-agent-self-evolution`, or a sibling directory. With
`BORT_ALLOW_BROADCAST` unset the whole loop is a dry run (the skill is still
pinned to IPFS; both chain writes only simulate).

| Flag | Effect |
|---|---|
| `--iterations N` | GEPA iterations (default 10). |
| `--repo PATH` | Path to the `hermes-agent-self-evolution` repo. |
| `--commit-only` | Skip the optimizer; commit the latest existing run dir. |
| `--only {both,evolution,learning}` | Which on-chain steps to run (default `both`). |
| `--min-improvement F` | Only commit if `metrics.improvement >= F`. |

If `commit_evolution` succeeds but `commit_learning` fails, retry just the
learning step: `hermes bort evolve <skill> --token-id N --commit-only --only learning`.
Each run is a distinct on-chain event; the knowledge registry does not dedup.

## Tools

### Read

| Tool | What it does |
|---|---|
| `bort_read_agent(token_id, include_logic_details=true, include_trades=true)` | Full state: BAP-578 state, IPFS identity, trade summary + PnL, knowledge sources, learning metrics, logic-specific state (Hunter positions / CTO campaign / Trading vault). |
| `bort_health_check()` | CircuitBreaker preflight: global pause + per-contract pause for the active logic contracts. Cheap, mandatory before any write. |
| `bort_list_actions(logic_name)` | Action catalog for `"Hunter"` / `"Trading V5"` / `"CTO"`, split into on-chain and off-chain. |
| `bort_marketplace_browse(sort?, limit?, offset?, seller?, include_recent_sales?)` | MarketplaceV3 listings + recent sales. No wallet. |
| `bort_marketplace_agent(token_id, include_offers?, include_activity?)` | One agent's active listing, open offers, recent marketplace events. |
| `bort_list_agent_uri(token_id, intent)` | Dapp deep link + step list for owner-signed actions. `intent` is `list` / `manage` / `view` / `offers`. |

### Write

All writes preflight `globalPause`, contract pause, `VPMv2.canForward`, policy
disposition, and (when `confirm`) the Hermes approval prompt. With
`BORT_ALLOW_BROADCAST` unset they return `simulated_only`.

| Tool | What it does |
|---|---|
| `bort_grant_permission_uri(token_id, operator, ...)` | Builds the two owner-signed transactions to give the operator a `WRITE` grant on the agent's vault. |
| `bort_commit_learning(token_id, data_hash, ...)` | Emits a `LearningRecorded` event on the logic contract via `record_learning` (Hunter / Trading V5 / CTO). A permanent on-chain learning record; mutates no score. |
| `bort_invoke(token_id, action, args)` | Universal write: any catalogued `handleAction` through `VPMv2.forwardHandleAction`. 13 actions wired today. |
| `bort_anchor_memory(token_id)` | Pins the agent's local JSONL memory buffer to IPFS and writes a `MEMORY` source through `KnowledgeRegistryV2.addKnowledgeSourceDelegated`. |
| `bort_commit_evolution(token_id, output_dir)` | Anchors a `hermes-agent-self-evolution` run (`output/<skill>/<timestamp>/evolved_skill.md` + `metrics.json`) as an `INSTRUCTION` knowledge source. |

## Examples

### Read an agent

```python
from hermes_bort.tools.read_agent import handle as read_agent
import json, asyncio

state = json.loads(asyncio.run(read_agent({"token_id": 11100})))
print(state["on_chain"]["state"]["owner"])
print(state["logic"]["name"], state["logic"]["snapshot"])
```

### Browse the marketplace

```python
from hermes_bort.tools.marketplace_browse import handle as browse
import json, asyncio

raw = asyncio.run(browse({"sort": "price_asc", "limit": 10}))
for row in json.loads(raw)["listings"]:
    print(row["token_id"], row["price_per_token"], row["lister"])
```

### Drive an action through the operator

Requires the agent owner to have already run `bort_grant_permission_uri` and signed
the two grant transactions.

```python
from hermes_bort.tools.invoke import handle as invoke
import json, asyncio

raw = asyncio.run(invoke({
    "token_id": 11100,
    "action": "buy_token",
    "args": {"token_address": "0x...", "amount_bnb_wei": "10000000000000000"},
}))
result = json.loads(raw)
print(result["status"])           # 'broadcast' / 'simulated_only' / 'blocked'
print(result.get("tx_hash"))
```

### Anchor session memory to IPFS

After the agent owner has granted the operator a WRITE permission, this turns the
local JSONL buffer into an on-chain `MEMORY` knowledge source the next session
will prefetch.

```bash
hermes bort anchor-memory --token-id 11100
```

### Generate a marketplace deep link

```python
from hermes_bort.tools.list_agent_uri import handle as list_uri
import json, asyncio

raw = asyncio.run(list_uri({"token_id": 11100, "intent": "list"}))
print(json.loads(raw)["url"])     # https://www.bortagent.xyz/#/my-listings
```

## Memory portability

`BortMemoryProvider` keys session memory by the agent's `tokenId` (passed to
Hermes as `agent_identity` at session start).

- During a chat: turns go into `~/.hermes/bort-memory/<tokenId>.jsonl`.
- On session end (or `bort_anchor_memory`): the buffer is pinned to IPFS via
  Pinata and committed to `KnowledgeRegistryV2` as a `MEMORY` source through the
  operator's WRITE grant.
- On next session start: the provider pulls active `MEMORY` sources from
  `KnowledgeRegistry.getActiveKnowledgeSources(tokenId)` and exposes them to
  prefetch.

The agent's memory travels with the NFT: a buyer who imports the same tokenId in
their own Hermes instance picks up the same history without anyone sharing files.

## Threat model

A BORT agent NFT carries text that the owner controls: the IPFS-pinned identity
(name, description, attribute values), `KnowledgeRegistry` source descriptions,
and the IPFS content of those sources. All of it can reach the LLM through
`bort_read_agent` results and `BortMemoryProvider.prefetch()` output. Hermes
itself does not sanitize tool returns or memory prefetch: whatever a plugin
gives back is injected into context as-is.

The plugin treats those surfaces as untrusted and applies a narrow data-boundary
layer in `hermes_bort.bort_sanitize`:

- Free-form text fields (identity `name` / `description` / `external_url`,
  knowledge source `description`, prefetched memory blocks) are wrapped in an
  `<external-data source="...">...</external-data>` envelope with a one-line
  preamble telling the model to treat the contents as data, not instructions.
- Each string is capped at 2 KB with an explicit `[truncated, N chars total]`
  marker so an attacker cannot flood the context.
- C0 controls, ANSI escape sequences, zero-width characters, and bidi-override
  Unicode are stripped before wrapping (same character set Hermes' own cron
  scanner blocks).
- Numeric ids, addresses, on-chain enums, and structural keys are not wrapped:
  only the strings that came from owner-controlled inputs.

This raises the bar from "trivial injection" to "the model has to ignore an
explicit data-boundary marker." It is not a complete defense, and it is not
meant to be: the operator-key boundary below is what binds what a tricked model
can actually cause on-chain.

## Security boundary

The plugin holds the operator key, not the NFT owner's key. The operator can
only do what the owner has granted in `VaultPermissionManagerV2` and what
`~/.hermes/bort-policy.yaml` allows. The Hermes approval prompt fires when an
action's disposition is `confirm`. With `BORT_ALLOW_BROADCAST` unset, every
write returns `simulated_only`.

A leaked operator key can be revoked by the owner with one `revokePermission`
transaction in VPM v2; it cannot mint, transfer, or list the NFT. The plugin
never writes the key to disk: `hermes bort init-operator` prints it once to
stdout for the user to set in their env.

## Configuration

Read from `plugin.yaml` or environment.

| Setting | Env var | Default |
|---|---|---|
| Runtime API base | `BORT_API_URL` | `https://bap578-nfa-platform.onrender.com` |
| BSC RPC | `BSC_RPC_URL` | `https://bsc-dataseed.binance.org` |
| Dapp URL (deep links) | `BORT_DAPP_URL` | `https://www.bortagent.xyz` |
| Memory dir | `BORT_MEMORY_DIR` | `~/.hermes/bort-memory` |
| Operator key | `BORT_OPERATOR_PRIVATE_KEY` | unset (writes disabled) |
| Allow broadcast | `BORT_ALLOW_BROADCAST` | unset (simulate only) |
| Policy file | `BORT_POLICY_PATH` | `~/.hermes/bort-policy.yaml` |
| Pinata key | `PINATA_API_KEY` | unset (needed for anchor tools) |
| Pinata secret | `PINATA_API_SECRET` | unset (needed for anchor tools) |

## Test

```bash
BORT_TEST_TOKEN_ID=11100 pytest -q
```

99 tests. Most are integration-style: they hit BSC mainnet RPC and the BORT
runtime API and verify the plugin works end-to-end against live infrastructure.
Network tests are skipped or short-circuit when the relevant env var is unset
(e.g. Pinata pin tools return a clear error without `PINATA_API_KEY`).

## Architecture

```
hermes_bort/
  plugin.yaml                 metadata + config keys
  __init__.py                 register(ctx) entry point
  bort_chain.py               web3.py reads + VPM v2 / KR v2 ABIs
  bort_ipfs.py                Pinata pin + dual-gateway fetch (pinata, ipfs.io)
  bort_api.py                 async HTTP client to the BORT runtime API
  bort_logic_adapters.py      HunterAdapter / TradingV5Adapter / CTOAdapter
  bort_kr.py                  KR v2 delegated-write helper
  bort_marketplace.py         MarketplaceV3 constants + dapp deep-link builders
  action_codec.py             encode/decode the 13 handleAction payloads
  bort_signer.py              operator key load + broadcast
  bort_policy.py              ~/.hermes/bort-policy.yaml loader + writer
  approval.py                 Hermes approval prompt integration
  cli.py                      hermes bort init-operator / init-policy / doctor / anchor-memory / commit-evolution / evolve
  evolution_loop.py           self-evolution optimizer runner + on-chain commit chain
  tools/
    read_agent.py health_check.py list_actions.py
    marketplace_browse.py marketplace_agent.py list_agent_uri.py
    grant_permission.py commit_learning.py invoke.py
    anchor_memory.py commit_evolution.py
  memory/
    provider.py               BortMemoryProvider
```

The plugin is thin on purpose. BORT's runtime API at
`bap578-nfa-platform.onrender.com` already exposes most data as public read
endpoints; this plugin uses those instead of re-implementing them. Direct
on-chain calls are reserved for what the runtime does not expose: `tokenURI`,
`KnowledgeRegistry.getActiveKnowledgeSources`, `CircuitBreaker.globalPause`, the
VPM v2 forwarder, and KR v2 delegated writes.

## License

MIT. See [LICENSE](LICENSE).
