# sdlc-graph

**A personal AI assistant powered by a knowledge-base graph, for the whole software development lifecycle, and for the knowledge around it.**

Inspired by [graaph.org](https://graaph.org/), but aimed past the coding assistant. Code is one kind of knowledge among many. `sdlc-graph` treats everything you produce and consume while building software as nodes in one graph. That includes requirements, design decisions, code, tests, bugs, meetings, papers, and half-formed ideas. The assistant reasons over that graph instead of over a single file or chat window.

> Status: hackathon project, early prototype.

---

## Why

Most AI coding assistants see a snapshot: the open file, a few neighbors, a chat history. The things that make software work sit outside that window:

- *Why* was this designed this way? (a decision made three months ago)
- Which requirement does this function satisfy, and which tests cover it?
- Which paper, ticket, or Slack thread explains this odd workaround?
- What did I learn last time I hit this class of bug?

That knowledge is scattered across repos, docs, tickets, notes, and your head. It is **relational**, so a graph is a natural way to store it. `sdlc-graph` makes the graph the assistant's long-term memory and its source of grounding.

## Vision

1. **One graph, many artifact types.** Code, docs, tickets, decisions, notes, and external references live together and are linked.
2. **SDLC-aware.** The graph models the lifecycle (requirement → design → implementation → test → release → incident → retro), so traceability comes for free.
3. **Personal first.** It runs locally, is owned by you, and is plain-file persistent. It is a second brain for how you build.
4. **Graph-grounded answers.** The LLM retrieves a *subgraph* (a node plus its neighborhood and the paths between nodes) as context, not just top-k text chunks. Answers can cite the nodes they used.
5. **Beyond coding.** The same engine handles knowledge management: reading notes, research, meeting outcomes, and personal retrospectives.

## Core ideas

### Built on graphify, adapted per domain

The engine is [graphify](https://github.com/Graphify-Labs/graphify), used as a library from PyPI. It supplies file detection, tree-sitter extraction for about 25 languages, LLM semantic extraction with pluggable backends, entity dedup, Leiden clustering, graph queries and export. The graph is a NetworkX graph throughout.

`sdlc-graph` is a thin layer that adapts it to a **domain** by supplying a *domain pack*, a YAML file with two halves:

| Layer | What the pack defines | Example (`sdlc`) |
|---|---|---|
| **Vocabulary** | stages (a constrained tag set) and their flow, legal relations, gap rules, path classifiers | requirement → design → build → test → release → operate; `test --verifies--> requirement` |
| **Processing** | which artifact types go through which processor and LLM backend | `*.py` → tree-sitter; `*.md` → frontmatter; `*.pdf` → LLM (`claude-cli`) |

Shipped packs (`src/sdlc_graph/domains/`): **`sdlc`**, **`research`** (question → literature → hypothesis → experiment → analysis → write-up, where code is an experiment) and **`publishing`** (idea → outline → draft → edit → publish → promote). A new domain is a new YAML file. A workspace can mix domains, since each project picks its own pack, and all of them land in one graph.

### Graph model

- **Process layer**: `stage:<domain>:<id>` nodes joined by `precedes`, and `project:<name>` nodes joined to the stages they take part in.
- **Knowledge layer**: artifacts (files, symbols, documents, and typed nodes such as requirements, decisions, questions and hypotheses) with a `kind`, a `project`, and `stages`, a **list of stage tags** validated against the pack's vocabulary. A node may carry several tags, such as `[requirement, design]`. Each tag is also an `in_stage` edge.
- Edges carry `relation` and `confidence`: `EXTRACTED` (explicit in source), `INFERRED` (derived or LLM-suggested), or `AMBIGUOUS`.
- Analysis and clustering run on the knowledge layer only, because stage and project nodes are hubs that would skew them.

### Explicit links, derived links, gaps

- **Frontmatter**: `satisfies: [REQ-001]` in a markdown file creates an edge. Relation names come from the pack's `flow_relations`.
- **Annotations**: `# verifies: REQ-001` in any code file does the same.
- **Derived**: a test file that imports a code file yields an `INFERRED` `verifies` edge, and the rule comes from the pack's `flow_relations` rather than from code.
- **Gaps**: the pack's `expectations` ("every requirement needs a test") are evaluated as plain graph queries. Rules can skip trivial files (`min_symbols`, `exclude`), and broken references are reported too.
- **LLM work items** (`--semantic`): the `llm_items` processor reads planning and design prose (roadmaps, todos, design notes) with the pack's own prompt and extracts requirements, decisions and tasks as `INFERRED` nodes (`origin: llm`, with `status: done|open`) tagged with the pack's stages. They link to their source document with `derived_from`, are cached by prompt hash, and don't trigger gap rules until confirmed.
- **Cross-project links**: byte-identical files get `identical_to` and same-path files that differ get `variant_of`, so clones of a template across repos show up (`sdlc-graph clones`).
- **Ignore rules**: `.gitignore`, `.graphifyignore`, the pack's default `ignore:` (logs, locks, db files) and a per-project `ignore:` list in the workspace file.

### Retrieval: graph + semantic (planned)

Seed nodes by keyword and embedding, expand through the graph, rank and prune, then pass the subgraph to the LLM with node IDs so answers can cite them. graphify's `serve.py` already does the query and subgraph-to-text parts.

## What you can ask

- *"Why does `login()` use OIDC?"* → follows `implements` → `satisfies` → `decided_by` to the ADR.
- *"What breaks if I change `User.email`?"* → impact analysis over `depends_on`, `calls`, and `tests`.
- *"Which requirements have no tests?"* → graph query for coverage gaps.
- *"Summarize what I learned about rate limiting this month."* → knowledge management across notes and sources.
- *"Draft a design doc for X using what I already know."* → generation grounded in the subgraph.
- *"Show me orphaned decisions and stale docs."* → graph health checks.

## Architecture

```
            ┌──────────────┐
 sources ──▶│  Ingestors   │  code · git · markdown · notes · web
            └──────┬───────┘
                   ▼
            ┌──────────────┐      ┌────────────┐
            │ Knowledge    │◀────▶│ Persistence│  GraphML / JSON (+ SQLite later)
            │ Graph        │      └────────────┘
            │ (NetworkX)   │
            └──────┬───────┘
                   ▼
            ┌──────────────┐      ┌────────────┐
            │  Retriever   │◀────▶│ Embeddings │  local or API
            └──────┬───────┘      └────────────┘
                   ▼
            ┌──────────────┐
            │  Assistant   │  LLM + tools (query, link, summarize, generate)
            └──────┬───────┘
                   ▼
              CLI · Web UI · graph visualization
```

## Tech stack

Python 3.10+ (conda env), NetworkX, graphify (PyPI release), PyYAML. LLM backends come from graphify: Claude (API or `claude-cli`), Bedrock, Gemini, OpenAI, Kimi and Ollama.

## Documentation

- [docs/GUIDE/SETUP.md](docs/GUIDE/SETUP.md): installation, `cc_cli` (subscription) and Bedrock setup, troubleshooting
- [docs/GUIDE/USER-GUIDE.md](docs/GUIDE/USER-GUIDE.md): getting started, workspace files, linking conventions, commands, customizing domain packs

## Quickstart

```bash
conda create -n graf python=3.11 pip -y && conda activate graf   # dedicated env, see docs/GUIDE/SETUP.md
python -m pip install -e ".[dev]"
sdlc-graph doctor                                      # confirm you are in graf, not conda base

sdlc-graph domains                                     # list domain packs
sdlc-graph build -c examples/workspace.yaml            # build the graph, write sdlc-graph-out/graph.json
sdlc-graph gaps  -c examples/workspace.yaml            # report gaps per domain rules
sdlc-graph clones -c examples/workspace.yaml           # files shared across projects
sdlc-graph open --semantic --adapter cc_cli -c examples/acme-shop/workspace.yaml   # open work items via the LLM pass
python -m pytest
```

`examples/` holds a small SDLC project (`acme-shop`) and a research project (`rq-demo`) with deliberate gaps, plus a workspace that builds both into one graph. LLM processors are off unless you pass `--semantic`.

## LLM backends (home vs work)

All LLM calls go through one adapter, `src/sdlc_graph/llm.py`. The backend is chosen in this order: explicit argument, then the `SDLC_GRAPH_BACKEND` environment variable, then the project's `backend:` in `workspace.yaml`, then the processor's `backend:` in the domain pack, then the default `cc_cli`.

| Backend | Billing | Notes |
|---|---|---|
| `cc_cli` (default) | **Claude Code subscription** | Our own, ported from SPL.py's adapter. Strips `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_USE_BEDROCK/VERTEX/FOUNDRY` and the nested-session guards from the environment, so `claude` always uses your stored login. Prompt via stdin, tools off, launch retries, distinct rate-limit error. |
| `claude-cli` | whatever `claude` picks up | graphify's built-in. Passes the environment through, so with `ANTHROPIC_API_KEY` set it **bills the API per token**. Prefer `cc_cli`. |
| `claude`, `gemini`, `openai`, `kimi` | API, per token | graphify's, need API keys |
| `bedrock` | your AWS account | graphify's |
| `ollama` | local | graphify's |

```bash
# home: subscription, nothing to set
sdlc-graph build --semantic -c workspace.yaml

# work: AWS Bedrock, same workspace file, no YAML edits
python -m pip install -e ".[bedrock]"
export SDLC_GRAPH_BACKEND=bedrock AWS_PROFILE=<profile> AWS_REGION=<region>
export GRAPHIFY_BEDROCK_MODEL=<model-or-inference-profile-id>   # graphify's built-in default is an old model
sdlc-graph llm -c workspace.yaml          # resolved backend, billing mode and readiness checks per processor
sdlc-graph build --semantic -c workspace.yaml
```

`cc_cli` model and timeout: `SDLC_GRAPH_CC_MODEL` (default `claude-sonnet-5`) and `SDLC_GRAPH_CC_TIMEOUT` (default 1200 s). LLM results are cached by backend and prompt, so switching backends re-extracts once. A session or rate limit stops the LLM pass with a warning instead of failing the build; rerun later and the cached documents are skipped.

## Layout

```
src/sdlc_graph/
  domains/*.yaml   # domain packs: sdlc, research, publishing
  domain.py        # Domain / Workspace loading, stage-tag validation, path classifiers
  llm.py           # LLM adapter: backend resolution + billing labels (cc_cli at home, bedrock at work)
  cc_cli.py        # subscription-only Claude Code backend (ported from SPL.py)
  processors.py    # processing layer: ast, frontmatter, annotations, llm, llm_items
  links.py         # cross-project identical_to / variant_of edges
  pipeline.py      # tag + link + derive flow edges + cluster (wraps graphify)
  analyze.py       # gap analysis from a pack's expectations
  cli.py
examples/          # acme-shop (sdlc), rq-demo (research), workspace.yaml (both)
```

## Hackathon scope

**Must have (MVP)**
- [x] Domain packs (`sdlc`, `research`, `publishing`) with constrained stage tags
- [x] Ingest code (tree-sitter) and Markdown with frontmatter; annotations; derived `verifies` edges
- [x] Gap analysis and broken-reference reporting
- [x] Persist the graph (`graph.json`); reload is not implemented yet
- [ ] `ask` command: seed → expand → LLM answer with cited nodes
- [ ] Interactive graph visualization

**Should have**
- [ ] Git history ingestion (commits ↔ files ↔ issues)
- [ ] LLM-proposed edges with accept/reject review
- [ ] Traceability and coverage-gap reports (requirement → code → test)
- [ ] Impact analysis ("what depends on this?")

**Stretch**
- [ ] Watch mode: incrementally update the graph as files change
- [ ] Editor/MCP integration so coding assistants can query the graph
- [ ] Multi-language code parsing via tree-sitter
- [ ] Graph health dashboard (orphans, stale docs, superseded decisions)

## Demo story

1. Point it at a small real repo plus a few ADRs and meeting notes.
2. Show the graph as it forms.
3. Ask a "why" question that spans code, a decision, and a note, which no single-file assistant could answer.
4. Change a function and show the impact analysis.
5. Show the coverage-gap report and an LLM-suggested missing link.

## Design principles

- **Provenance matters.** Every edge records whether it was extracted, inferred, or user-confirmed.
- **Human in the loop.** The LLM proposes links, and you decide which ones become trusted.
- **Local and portable.** The graph is a file you own. Diff it, version it, and back it up.
- **Cite or stay quiet.** Answers reference graph nodes, and gaps are admitted.

## Acknowledgments

- [graaph.org](https://graaph.org/) for the inspiration
- [NetworkX](https://networkx.org/)

## License

TBD
