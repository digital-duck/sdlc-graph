# Setup

How to install `workgraph`, the knowledge layer that sits beside Claude Code, and get a working LLM backend. For usage, see [USER-GUIDE.md](USER-GUIDE.md); for a guided walk-through, [TUTORIAL.md](TUTORIAL.md).

## 1. Prerequisites

| Need | Version | Check |
|---|---|---|
| conda (Anaconda or Miniconda) | any | `conda --version` |
| git | any | `git --version` |
| Claude Code CLI | any recent, logged in (for the `cc_cli` backend) | `claude auth status` |

Python 3.10 to 3.12 comes with the conda environment in step 3. You only need the Claude Code CLI if you run the LLM pass (`--semantic`) with `cc_cli`, or if you want workgraph to act as the memory of your Claude Code sessions (step 8). The structural build (code, markdown, gaps, clones) needs no LLM at all.

Optional tools, each for one feature:

| Tool | For | Check |
|---|---|---|
| Ollama with an embedding model | `workgraph search --semantic` (local, free) | `ollama pull nomic-embed-text` |
| `gh` (GitHub CLI), logged in | `workgraph sync init` verifying that the repository is private | `gh auth status` |
| A **private** GitHub repository | syncing a workgraph between machines | |

## 2. Get the code

```bash
mkdir -p ~/projects/digital-duck && cd ~/projects/digital-duck
git clone <this-repo-url> work-graph
```

`workgraph` uses [graphify](https://github.com/Graphify-Labs/graphify) as a library, installed automatically from PyPI (`graphifyy>=0.7.19`; the latest release is fine and is what the tests run against most often).

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

workgraph is a regular Python package. Install it from the clone (an editable install, so `git pull` updates it), always as `python -m pip` so the env's own pip is used:

```bash
cd ~/projects/digital-duck/work-graph
python -m pip install -e ".[dev]"
workgraph doctor
```

`workgraph doctor` prints the Python path, the environment, the graphify version and the default backend, and warns with `[!!]` if it is running in conda base. The environment line should read `.../envs/graf`.

The extras (combine as needed, for example `".[dev,bedrock]"`):

| Extra | Adds | When |
|---|---|---|
| `dev` | pytest | always, for running tests |
| `bedrock` | boto3 | only for the AWS Bedrock backend (work) |
| `leiden` | graspologic (Leiden community detection) | optional; see below |
| `mcp` | mcp | only if you later serve the graph over MCP |
| `ui` | streamlit | the `workgraph ui` app. Use a clean environment: a conda *base* with a NumPy 2 and a pyarrow built for NumPy 1 fails with `_ARRAY_API not found` when Streamlit draws tables |
| `semantic` | sentence-transformers | embeddings for `workgraph search --semantic` without Ollama (with Ollama: `ollama pull nomic-embed-text` instead) |

**About `leiden`.** Without it, community detection uses Louvain (built into NetworkX), which works fine. Leiden is slightly better, but graspologic is heavy: it brings scipy, numba, statsmodels, seaborn, gensim and pins `beartype<0.19`, which conflicts with other packages (for example `fastmcp`). Only add it in a dedicated env like `graf`, never in a shared one. It is not available on Python 3.13+.

## 4. Verify

```bash
workgraph domains
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
workgraph build -c examples/workspace.yaml
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
workgraph llm -c examples/acme-shop/workspace.yaml --adapter cc_cli
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
| `WORKGRAPH_CC_MODEL` | `claude-sonnet-5` | model passed to `claude --model`. workgraph does not read `ANTHROPIC_MODEL` |
| `WORKGRAPH_CC_TIMEOUT` | `1200` | seconds before a call is abandoned |
| `WORKGRAPH_BACKEND` | (unset) | force a backend for all commands, same as `--adapter` |

**Do not confuse `cc_cli` with `claude-cli`.** `claude-cli` is graphify's built-in backend and passes your whole environment through, so if `ANTHROPIC_API_KEY` is set it bills the API per token. `workgraph llm` flags that case. Use `cc_cli`.

## 6. LLM backend: `bedrock` (work, AWS)

```bash
python -m pip install -e ".[bedrock]"

export WORKGRAPH_BACKEND=bedrock          # or pass --adapter bedrock to each command
export AWS_PROFILE=<your-profile>          # or key env vars / SSO session
export AWS_REGION=<region>
export GRAPHIFY_BEDROCK_MODEL=<model-or-inference-profile-id>
```

Set `GRAPHIFY_BEDROCK_MODEL` explicitly. Graphify's built-in default is an old model (`anthropic.claude-3-5-sonnet-20241022-v2:0`) that may not be enabled in your account. If you use SSO, run `aws sso login --profile <your-profile>` first.

Verify:

```bash
workgraph llm -c workspace.yaml
```

Every line should show `[ok]`. The region and model lines show `[!!]` when you rely on defaults; that is informational. Document text is sent to Bedrock in your own AWS account.

Other graphify backends (`claude`, `gemini`, `openai`, `kimi`, `ollama`) also work with `--adapter <name>`, using the API keys graphify documents.

## 7. Where things are written

Everything lives in one **home folder**, `~/workgraph` by default. Set `WORKGRAPH_HOME=/some/dir` to move it. Create it with `workgraph init`.

| Path (under the home folder) | Content |
|---|---|
| `workspace.yaml` | the default workspace: the projects workgraph tracks (`workgraph track <path>`) |
| `graphs/` | layer files (org, product, process; JSON and YAML) |
| `workgraph.sqlite3` | the database: YAML comments, uid history, Claude Code session journal. It sits at the top of the home folder, outside `graphs/` and `sync/`, so it can never be committed or shared by accident. Do not put the home folder in Google Drive, iCloud or Dropbox |
| `sync/` | the git working tree for `workgraph sync` (text only), created by `sync init` |
| `out/graph.json` | the built graph |
| `cache/` | cached LLM results (keyed by backend and prompt) and graphify's AST cache |
| `src/` | clones of repositories given as `--src <URL>` |

`WORKGRAPH_CACHE=/some/dir` moves just the cache. Deleting the cache only costs you a re-run of the LLM pass. Your project folders are never written to. The repo's `examples/` has its own `graphs/` folder, which is used when you pass `-c examples/workspace.yaml`.

## 8. Connect Claude Code (optional)

This is what turns workgraph into a knowledge layer for Claude Code: your projects are tracked as you open them, each session starts with a summary of what the graph knows, and `/workgraph` queries it. Pick **one** route:

```bash
# A. the plugin (hooks + /workgraph:workgraph). NOT published to a marketplace yet while it is tested;
#    to try it from a clone, start Claude Code with the plugin folder:
claude --plugin-dir <path to this repo>/plugin
```

```bash
# B. plain settings (hooks + /workgraph): merges into ~/.claude/settings.json, keeping a .bak copy
workgraph claude-setup            # first look at what it would add
workgraph claude-setup --write
```

Then, once, bring in the work Claude Code has already done (a dry run first):

```bash
workgraph import-claude
workgraph import-claude --apply --with-titles
workgraph build
```

Both hooks only run `workgraph`, never block a session, and use the structural build (no LLM, no subscription use). Details: [USER-GUIDE.md](USER-GUIDE.md), "Using workgraph as the memory of Claude Code".

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
| `unknown LLM backend` | Typo in `--adapter` / `WORKGRAPH_BACKEND`; the error lists valid names. |
| `claude -p` or an LLM call fails with a model error | `ANTHROPIC_MODEL` in your shell points at a retired model. Unset it or set a current one; workgraph's own calls use `WORKGRAPH_CC_MODEL`. |
| `workgraph ui` fails with `_ARRAY_API` or `numpy.core.multiarray` | A NumPy 2 with a pyarrow built for NumPy 1, usually conda *base*. Use the `graf` environment and the `ui` extra. |
| `pytest` errors in `examples/` | Run `python -m pytest` from the repo root; it is configured to collect only `tests/`. |
| Build is slow or has thousands of nodes | Generated files are being ingested; see "Ignoring noise" in the [user guide](USER-GUIDE.md). |

### Repairing base

If an install already went into base, the packages it changed there can be put back. First remove what this project added, then restore the one that breaks other tools:

```bash
conda activate base
python -m pip uninstall -y workgraph graspologic graspologic-native
python -m pip install "beartype>=0.20.0"      # graspologic had pinned it to 0.18.5, breaking fastmcp
```

The same install also upgraded seaborn (0.12.2 to 0.13.2), joblib, scipy, numba, statsmodels and a few more. pip does not record earlier versions, so those cannot be restored automatically; they are normally backward compatible. If a notebook or tool in base misbehaves afterwards, reinstall the specific version it needs, for example `python -m pip install "seaborn==0.12.2"`.
