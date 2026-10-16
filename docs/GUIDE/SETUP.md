# Setup

How to install `sdlc-graph` and get a working LLM backend. For usage, see [USER-GUIDE.md](USER-GUIDE.md).

## 1. Prerequisites

| Need | Version | Check |
|---|---|---|
| conda (Anaconda or Miniconda) | any | `conda --version` |
| git | any | `git --version` |
| Claude Code CLI | any recent, logged in (for the `cc_cli` backend) | `claude auth status` |

Python 3.10 to 3.12 comes with the conda environment in step 3. You only need the Claude Code CLI if you run the LLM pass (`--semantic`) with `cc_cli`. The structural build (code, markdown, gaps, clones) needs no LLM at all.

## 2. Get the code

```bash
mkdir -p ~/projects/digital-duck && cd ~/projects/digital-duck
git clone <this-repo-url> sdlc-graph
```

`sdlc-graph` uses [graphify](https://github.com/Graphify-Labs/graphify) as a library, installed automatically from PyPI (`graphifyy>=0.7.19`; the latest release is fine and is what the tests run against most often).

## 3. Create the conda environment and install

Use a **dedicated environment** (`graf`) and give it Python and pip explicitly:

```bash
conda create -n graf python=3.11 pip -y
conda activate graf
```

> **Do not skip `python=3.11 pip`.** `conda create -n graf` on its own makes an env with *no* Python or pip. Your shell then falls through to the **base** environment's `pip`, and every package lands in base, upgrading things like seaborn, scipy and beartype there. If you already created `graf` that way, repair it with `conda install -n graf python=3.11 pip -y`.

Confirm you are really inside it before installing anything:

```bash
which python                                   # must be .../anaconda3/envs/graf/bin/python
python -m pip --version                        # must point into .../envs/graf/...
```

Then install from the repo root, always as `python -m pip` so the env's own pip is used:

```bash
cd ~/projects/digital-duck/sdlc-graph
python -m pip install -e ".[dev]"
sdlc-graph doctor
```

`sdlc-graph doctor` prints the Python path, the environment, the graphify version and the default backend, and warns with `[!!]` if it is running in conda base. The environment line should read `.../envs/graf`.

The extras (combine as needed, for example `".[dev,bedrock]"`):

| Extra | Adds | When |
|---|---|---|
| `dev` | pytest | always, for running tests |
| `bedrock` | boto3 | only for the AWS Bedrock backend (work) |
| `leiden` | graspologic (Leiden community detection) | optional; see below |
| `mcp` | mcp | only if you later serve the graph over MCP |

**About `leiden`.** Without it, community detection uses Louvain (built into NetworkX), which works fine. Leiden is slightly better, but graspologic is heavy: it brings scipy, numba, statsmodels, seaborn, gensim and pins `beartype<0.19`, which conflicts with other packages (for example `fastmcp`). Only add it in a dedicated env like `graf`, never in a shared one. It is not available on Python 3.13+.

## 4. Verify

```bash
sdlc-graph domains
```

Expected:

```
publishing  Publishing: idea → outline → draft → edit → publish → promote
research    Research: question → literature → hypothesis → experiment → analysis → writeup
sdlc        Software development lifecycle: requirement → design → build → test → release → operate
```

Then the tests and a first build on the bundled examples:

```bash
python -m pytest
sdlc-graph build -c examples/workspace.yaml
```

The tests should all pass. The build prints node and edge counts, a per-stage table, and one deliberate warning about a broken reference (`REQ-099`) in the example data.

## 5. LLM backend: `cc_cli` (home, Claude Code subscription)

`cc_cli` runs the `claude` CLI under your **subscription login**. It is the default backend, and it never uses the paid API: it strips `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_USE_BEDROCK`/`_VERTEX`/`_FOUNDRY` and the nested-session guards from the environment for each call.

**Log in once** (skip if `claude auth status` already shows `"loggedIn": true`):

```bash
claude          # follow the login prompt, choose your claude.ai account
```

**Check readiness** (offline, makes no model call):

```bash
sdlc-graph llm -c examples/acme-shop/workspace.yaml --adapter cc_cli
```

Expected:

```
acme-shop/doc-items (llm_items) -> cc_cli   billing: subscription (Claude Code login; API-key env vars stripped)
    [ok] `claude` on PATH: /path/to/claude
    [ok] subscription login: claude.ai / pro
    [ok] billing env vars stripped for the call: ...
```

If the login line says `not a subscription login`, run `claude` and use `/login` to sign in with your claude.ai account.

**Optional settings:**

| Variable | Default | Meaning |
|---|---|---|
| `SDLC_GRAPH_CC_MODEL` | `claude-sonnet-5` | model passed to `claude --model` |
| `SDLC_GRAPH_CC_TIMEOUT` | `1200` | seconds before a call is abandoned |
| `SDLC_GRAPH_BACKEND` | (unset) | force a backend for all commands, same as `--adapter` |

**Do not confuse `cc_cli` with `claude-cli`.** `claude-cli` is graphify's built-in backend and passes your whole environment through, so if `ANTHROPIC_API_KEY` is set it bills the API per token. `sdlc-graph llm` flags that case. Use `cc_cli`.

## 6. LLM backend: `bedrock` (work, AWS)

```bash
python -m pip install -e ".[bedrock]"

export SDLC_GRAPH_BACKEND=bedrock          # or pass --adapter bedrock to each command
export AWS_PROFILE=<your-profile>          # or key env vars / SSO session
export AWS_REGION=<region>
export GRAPHIFY_BEDROCK_MODEL=<model-or-inference-profile-id>
```

Set `GRAPHIFY_BEDROCK_MODEL` explicitly. Graphify's built-in default is an old model (`anthropic.claude-3-5-sonnet-20241022-v2:0`) that may not be enabled in your account. If you use SSO, run `aws sso login --profile <your-profile>` first.

Verify:

```bash
sdlc-graph llm -c workspace.yaml
```

Every line should show `[ok]`. The region and model lines show `[!!]` when you rely on defaults; that is informational. Document text is sent to Bedrock in your own AWS account.

Other graphify backends (`claude`, `gemini`, `openai`, `kimi`, `ollama`) also work with `--adapter <name>`, using the API keys graphify documents.

## 7. Where things are written

| Path | Content | Git |
|---|---|---|
| `sdlc-graph-out/graph.json` | the built graph | ignored |
| `sdlc-graph-out/cache/` | cached LLM results, keyed by backend and prompt | ignored |
| `graphify-out/` | graphify's own AST cache | ignored |

To keep the cache elsewhere, set `SDLC_GRAPH_CACHE=/some/dir`. Deleting the cache only costs you a re-run of the LLM pass. Your project folders are never written to.

## Troubleshooting

| Symptom | Fix |
|---|---|
| pip output mentions `anaconda3/lib/python3.11/site-packages` (not `envs/graf`), or `doctor` shows `[!!] environment` | You installed into conda **base**. Activate `graf`, check `which python`, and make sure the env has its own Python (step 3). See "Repairing base" below. |
| `pip's dependency resolver ... incompatible` errors listing packages you do not use here | Same cause: a shared env (base) with many unrelated packages. A fresh `graf` env has none of those conflicts. |
| `Ignoring invalid distribution ~vidia-cublas-cu12` warnings | Harmless leftovers of an interrupted install in that environment; unrelated to this project. In `graf` they do not appear. |
| `ModuleNotFoundError: graphify` | Reinstall in the active env: `python -m pip install -e ".[dev]"`. |
| `graspologic` fails to install | You are on Python 3.13+ or in a conflicted env. Skip the `leiden` extra; Louvain is used instead. |
| `claude on PATH` shows `[!!]` | Install Claude Code, make sure `claude` is on `PATH`, run `claude` once to log in. |
| `limit reached` warning during `--semantic` | Your subscription session or rate limit is hit. The build still finishes; rerun later and cached documents are skipped. |
| `unknown LLM backend` | Typo in `--adapter` / `SDLC_GRAPH_BACKEND`; the error lists valid names. |
| `pytest` errors in `examples/` | Run `python -m pytest` from the repo root; it is configured to collect only `tests/`. |
| Build is slow or has thousands of nodes | Generated files are being ingested; see "Ignoring noise" in the [user guide](USER-GUIDE.md). |

### Repairing base

If an install already went into base, the packages it changed there can be put back. First remove what this project added, then restore the one that breaks other tools:

```bash
conda activate base
python -m pip uninstall -y sdlc-graph graspologic graspologic-native
python -m pip install "beartype>=0.20.0"      # graspologic had pinned it to 0.18.5, breaking fastmcp
```

The same install also upgraded seaborn (0.12.2 to 0.13.2), joblib, scipy, numba, statsmodels and a few more. pip does not record earlier versions, so those cannot be restored automatically; they are normally backward compatible. If a notebook or tool in base misbehaves afterwards, reinstall the specific version it needs, for example `python -m pip install "seaborn==0.12.2"`.
