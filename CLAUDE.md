# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`workgraph` is a thin layer over [graphify](https://github.com/Graphify-Labs/graphify) (PyPI package `graphifyy`, used as a library) that builds a NetworkX knowledge graph of the software lifecycle and surrounding knowledge. Behavior is adapted per **domain pack** (YAML). Early hackathon prototype; see README.md for vision and roadmap (`ask`, visualization, graph reload are not implemented yet).

## Commands

```bash
conda create -n graf python=3.11 pip -y && conda activate graf   # dedicated env; always use `python -m pip`
python -m pip install -e ".[dev]"      # extras: dev, bedrock, leiden, mcp
workgraph doctor                      # warns [!!] if running in conda base

python -m pytest                       # all tests (tests/)
python -m pytest tests/test_pipeline.py::test_name   # single test

workgraph domains
workgraph gaps --src <path-or-git-url>              # ad-hoc: no workspace file needed (URLs are shallow-cloned into <home>/src)
workgraph init / track <path>                       # home folder + default workspace; `claude-setup` installs the Claude Code hooks
workgraph status / search "text" [--semantic] / show NODE / ui   # query the built graph
workgraph build -c examples/workspace.yaml          # writes <home>/out/graph.json (home = ~/workgraph or $WORKGRAPH_HOME)
workgraph gaps  -c examples/workspace.yaml
workgraph clones -c examples/workspace.yaml
workgraph check -c examples/workspace.yaml          # layer files + cross-graph references
workgraph convert examples/graphs/org.json          # JSON <-> YAML via the comment store
workgraph open --semantic --adapter cc_cli -c examples/acme-shop/workspace.yaml   # LLM work items
workgraph llm -c workspace.yaml       # resolved backend / billing / readiness per processor
```

No linter is configured. LLM processors only run with `--semantic`; the structural build needs no LLM.

## Architecture

Pipeline (`src/workgraph/pipeline.py::build_workspace`): for each project in a `Workspace` → `processors.run_processors` (per artifact type) → tag nodes with stages (`_tag`) → resolve cross-references (`_resolve_refs`) → `links.cross_project_links` → `derive_flow_edges` → graphify `build`/`cluster`. Clustering/analysis run on `knowledge_layer()` only, since stage/project hub nodes would skew it.

- **Domain packs** (`src/workgraph/domains/{sdlc,research,publishing}.yaml`, loaded by `domain.py`) have two halves: *vocabulary* (`stages`, `flow_relations`, `expectations` for gap rules, path `classifiers` → stage tags, `ignore`) and *processing* (artifact type → processor + LLM backend). Adding a domain = adding a YAML file (packaged via `package-data`). A workspace can mix domains per project.
- **Workspace files** (`workspace.yaml`, see `examples/`) list projects, each with a domain, optional `backend:` and `ignore:`.
- **Processors** (`processors.py`): `ast` (tree-sitter via graphify), `frontmatter`, `annotations` (`# verifies: REQ-1`), `llm`, `llm_items`. Relation names in frontmatter/annotations come from the pack's `flow_relations`; a test importing a code file yields an `INFERRED` `verifies` edge derived from those rules, not hardcoded. File walking honors `.gitignore`, `.graphifyignore`, the pack's `ignore`, and the project's `ignore`; symlinks leaving the project root are skipped.
- **Graph model**: process layer (`stage:<domain>:<id>`, `project:<name>` nodes) plus knowledge layer. Nodes carry `kind`, `project`, and `stages` (a *list* of tags validated against the pack). Edges carry `relation` and `confidence` (`EXTRACTED` / `INFERRED` / `AMBIGUOUS`). LLM-extracted items are `origin: llm`, `INFERRED`, and skipped by gap rules unless `include_llm`.
- **LLM adapter** (`llm.py`): backend resolution order is explicit arg → `WORKGRAPH_BACKEND` env → project `backend:` → processor `backend:` in pack → default `cc_cli`. `cc_cli.py` is a subscription-only Claude Code CLI backend that deliberately strips `ANTHROPIC_API_KEY`/Bedrock/etc. env vars so it never bills the API; do not switch it to pass the environment through. Env knobs: `WORKGRAPH_CC_MODEL`, `WORKGRAPH_CC_TIMEOUT`. A `BackendLimitError` stops the LLM pass with a warning rather than failing the build; results are cached by backend + prompt hash.
- `analyze.py::find_gaps` evaluates pack `expectations` as graph queries (plus broken references); `cli.py` is the argparse entry point (`workgraph.cli:main`).

`examples/acme-shop` (sdlc) and `examples/rq-demo` (research) contain deliberate gaps that tests and demos rely on; changing them can break tests.

**Layers** (design: `docs/DEV/README-design.md`): besides the repo graph, `graphs/*.json` next to the workspace file hold org/product/process layers (`schema.py` validates; ids are `<layer>:<path>`, nodes carry a `uid` used only for rename hints). `refs.merge_layers` runs at the end of `build_workspace`: it adds `process:<domain>` nodes from the packs, resolves cross-layer edges (including `/**` sub-graph endpoints) and tags every node with `layer`. JSON is canonical and what the loader reads; YAML is for review. `convert.py` moves between them, saving YAML comments in the database (`workgraph.sqlite3`) (`store.py`) anchored by item id so they are restored on JSON -> YAML.

**Home folder** (`paths.py`): all data lives under `$WORKGRAPH_HOME` (default `~/workgraph`): `workspace.yaml`, `graphs/` (layer files), `workgraph.sqlite3` (the database; top of the home folder, never inside `graphs/` or `sync/`), `sync/` (git tree for `syncing.py`), `out/graph.json`, `cache/` (LLM results and graphify's AST cache, passed as `cache_root` so nothing is written into projects or the cwd), `src/` (URL clones). Workspace resolution: `--src` > `-c` > `./workspace.yaml` > `<home>/workspace.yaml`. Tests redirect the home via an autouse fixture in `tests/conftest.py`.

**Claude Code integration** (`claude_code.py`, `tracking.py`): `workgraph hook session-start|session-end` are Claude Code hook handlers (JSON on stdin). Start tracks the project, logs the session and prints a graph summary that becomes session context; end records changed files and rebuilds in the background. Handlers must never raise or block. `claude-setup --write` merges them into `~/.claude/settings.json`; do not run that without the user asking.

**Search and UI** (`query.py`, `ui/app.py`): `query.py` is UI-free (load graph.json, `build_docs`, `keyword_search`, `semantic_search` with ollama / sbert / TF-IDF backends and an embedding cache in `<home>/cache/embeddings`, `neighbors`, `to_dot`, `gaps_for`). The Streamlit app only calls it, and works with both old and new Streamlit (`WIDE` helper). `ui/` tests use `streamlit.testing.v1.AppTest` and need a working numpy/pyarrow, so run them in a clean env.

**Plugin** (`plugin/`; deliberately NOT published: no `.claude-plugin/marketplace.json` in the repo, the draft is `docs/DEV/marketplace.draft.json`; do not add one or advertise `/plugin marketplace add` until the owner says so): hooks plus `commands/workgraph.md`, which must stay identical to `src/workgraph/data/workgraph-command.md` (a test checks). Plugin commands are namespaced (`/workgraph:workgraph`); `claude-setup --write` installs the plain `/workgraph` as a user command. Validate with `claude plugin validate plugin`. Background rebuilds always pass `-c <home>/workspace.yaml`, never the cwd's.

Further docs: `docs/GUIDE/SETUP.md` (install, backends, troubleshooting) and `docs/GUIDE/USER-GUIDE.md` (workspace files, linking conventions, customizing packs).
