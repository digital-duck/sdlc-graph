# workgraph

**A personal AI assistant powered by a knowledge-base graph, for the whole software development lifecycle, and for the knowledge around it.**

Inspired by [graaph.org](https://graaph.org/), but aimed past the coding assistant. Code is one kind of knowledge among many. `workgraph` treats everything you produce and consume while building software as nodes in one graph. That includes requirements, design decisions, code, tests, bugs, meetings, papers, and half-formed ideas. The assistant reasons over that graph instead of over a single file or chat window.

> Status: hackathon project, early prototype.

---

## Why

Most AI coding assistants see a snapshot: the open file, a few neighbors, a chat history. The things that make software work sit outside that window:

- *Why* was this designed this way? (a decision made three months ago)
- Which requirement does this function satisfy, and which tests cover it?
- Which paper, ticket, or Slack thread explains this odd workaround?
- What did I learn last time I hit this class of bug?

That knowledge is scattered across repos, docs, tickets, notes, machines, and your head. It is **relational**, so a graph is a natural way to store it. `workgraph` makes the graph the assistant's long-term memory and its source of grounding.

## Vision

1. **One graph, many artifact types.** Code, docs, tickets, decisions, notes, and external references live together and are linked.
2. **SDLC-aware.** The graph models the lifecycle (requirement → design → implementation → test → release → incident → retro), so traceability comes for free.
3. **Personal first.** It runs locally, is owned by you, and is plain-file persistent. It is a second brain for how you build.
4. **Graph-grounded answers.** The LLM retrieves a *subgraph* (a node plus its neighborhood and the paths between nodes) as context, not just top-k text chunks. Answers can cite the nodes they used.
5. **Beyond coding.** The same engine handles knowledge management: reading notes, research, meeting outcomes, and personal retrospectives.
6. **Many graphs, one runtime.** The repo graph is the first of several layered graphs (below). They reference each other instead of copying nodes.
7. **Two formats, one model.** JSON is the canonical machine format, and YAML is the human format for review and audit.
8. **Graph artifacts drive AI workflow.** The graph files are the working memory of the AI-assisted process, not a report generated at the end (below).

### Graph artifacts drive the AI workflow

Chat history and context windows are temporary, and each new session starts cold. The graph is **persistent context**: a file that outlives any one conversation and is shared by every tool that reads it.

- **Persistent context for LLMs and coding agents.** Before a task, an agent loads the relevant sub-graph (the requirement, its design decision, the code that implements it, the tests that verify it, the team that owns it) instead of re-discovering them from raw files. Every session starts from the same grounded state.
- **The graph steers the work.** Gap rules ("requirement has no test"), process stages and open work items tell the agent what to do next. The agent's output (a new design note, a test, an updated status) is written back into the graph, so the next step starts from it.
- **Human in the loop.** LLM-proposed nodes and edges are marked `INFERRED` until a person confirms them in the YAML review format. Agents and people read and write the same artifacts.
- **Tool-neutral.** Any LLM or coding agent can use the same files, whether it is Claude Code, another assistant or a script. Nothing depends on one tool's private memory.

This is the same graph-first approach as ConceptBook (a book as a graph) and Graph-Code (a blueprint as a graph), applied to the software development process itself. The wider vision is in `README-graph-vision.md` in the ZiNets repository.

### Search and review

`workgraph search "checkout test"` is a keyword search (names, paths, summaries and the first lines of each file, with phrase and prefix matching). `workgraph search "how are carts stored?" --semantic` ranks by meaning with embeddings from a local Ollama model (default `nomic-embed-text`) or sentence-transformers, and says plainly when it had to fall back to lexical TF-IDF. `workgraph show REQ-002` prints one node and its links. `workgraph ui` opens a Streamlit app to browse, filter, search, inspect gaps and see a node's neighbourhood.

### Memory for Claude Code

Two Claude Code hooks (installed by the `workgraph` plugin or `workgraph claude-setup`) make the graph the memory of your sessions. When a session starts, the project is tracked automatically and a short summary of what the graph knows about it (stages, gaps, open items, product, owner, what earlier sessions changed) is added to the session context. When the session ends, what changed is recorded and the graph is rebuilt in the background. Work done through Claude Code then flows into the same graph as everything else, with no manual ingestion. The `/workgraph` slash command queries the graph from inside a session. Data lives in `~/workgraph` (override with `WORKGRAPH_HOME`), and any local folder or git URL can be ingested with `--src`. `workgraph import-claude` backfills the work Claude Code has already done (the projects it worked in, session metadata, and its memory files as searchable nodes).

### Several machines

The database never leaves the machine. What cannot be rebuilt (layer files, YAML comments, the portable workspace) travels as text: `workgraph export` / `import` for a first setup by USB, then `workgraph sync push` / `pull` through a **private** GitHub repository. Exports and pushes are scanned and stop on databases, large files or anything shaped like a key; the org layer, session journal and LLM cache are opt-in. See the User Guide, "Moving to another machine".

`workgraph claude-setup --write` installs the hooks and the `/workgraph` command into your user-level Claude Code settings. The same pieces also exist as a Claude Code plugin in `plugin/`, which is **not published to a marketplace yet** while it is being tested; to try it from a clone, run `claude --plugin-dir <path>/plugin`. Use one route or the other, not both.

### Multiple graphs

Today's graph covers repositories. The vision is a set of graphs, each with its own vocabulary (a domain pack) and its own owner, that reference one another:

| Graph | Describes | Example hierarchy |
|---|---|---|
| **Repo** | code, docs, tests, decisions inside repositories (built today) | workspace > project > file > symbol |
| **Product** | what is shipped and how it is organized | product family > product > application |
| **Org** | who builds and owns it | company > division > department > group > team > person |
| **Process** | how work is delivered, as a reusable template | stakeholder interview > requirement spec > solution design > prototyping > build > test > release > feedback |

- **The SDLC is one process among many.** The `sdlc`, `research` and `publishing` packs are already small process graphs. A process graph generalizes them, and a new delivery process is a new pack.
- **Template versus instance.** A process graph defines the template ("design follows requirements"). A product's progress through it is an instance with status, owners and artifacts. The two are kept apart so that "what the process says" is never confused with "where we are".
- **Hierarchies are containment.** Each level is joined to its parent by `part_of`. A **sub-graph** is a node plus its descendants, so a team, a product or a single process stage can be addressed, queried and exported on its own.
- **Graphs reference each other.** A repo says `belongs_to: product:checkout`. A product says `owned_by: org:acme/payments`. A product says `follows: process:sdlc`. Org and process nodes are defined once and referenced everywhere, so the same team or stage never exists as several copies.

### Graph references

References exist for brevity in the spec. At run time they are always resolved in memory:

1. **Spec time.** A graph file refers to a node or a whole sub-graph in another graph by a namespaced ID (`org:acme/payments`, `process:sdlc`) instead of repeating it.
2. **Load time.** The loader resolves every reference and expands sub-graph references, merging all layers into **one in-memory graph**. Each node keeps a `layer` attribute, so queries, clustering and gap analysis work across layers without special cases.
3. **Integrity.** An unresolvable reference (renamed, moved or deleted target) is reported as a broken reference, like the broken-reference check that already exists for requirements. Stable IDs matter here, because path-style IDs break when a team is reorganized.

### Dual formats: JSON and YAML

Each graph is stored in two interchangeable formats:

| Format | For | Used for |
|---|---|---|
| **JSON** | machines | the canonical form: internal schema validation, referential integrity checks, tooling, and the `graph.json` already written by `build` |
| **YAML** | people | review and audit: more readable, and reviewers can add comments and annotations |

Converters go both ways (JSON <-> YAML). The conversion must be lossless for graph content. Validation always runs on the JSON form, and the human edits made in YAML flow back through the same validation.

**Comment store.** YAML comments have no JSON equivalent, so a plain YAML-to-JSON conversion would lose them. A small database (SQLite) tracks every graph file and keeps its comments:

- **Registry.** One row per `.json`/`.yaml` graph file, with its path, layer and content hash, so the tool knows which pairs belong together and when one side has changed.
- **Comments.** When YAML is converted to JSON, each comment is saved with an anchor (file, node ID, key path, and whether it is a leading, trailing or block comment) instead of a line number, so it survives reordering and reformatting.
- **Restore.** When JSON is converted back to YAML, the saved comments are re-attached by anchor, so the original comments return even though the JSON never held them.
- **Orphans.** If the node a comment was anchored to has been deleted or renamed, the comment is kept and reported as orphaned instead of being silently dropped.

## Core ideas

### Built on graphify, adapted per domain

The engine is [graphify](https://github.com/Graphify-Labs/graphify), used as a library from PyPI. It supplies file detection, tree-sitter extraction for about 25 languages, LLM semantic extraction with pluggable backends, entity dedup, Leiden clustering, graph queries and export. The graph is a NetworkX graph throughout.

`workgraph` is a thin layer that adapts it to a **domain** by supplying a *domain pack*, a YAML file with two halves:

| Layer | What the pack defines | Example (`sdlc`) |
|---|---|---|
| **Vocabulary** | stages (a constrained tag set) and their flow, legal relations, gap rules, path classifiers | requirement → design → build → test → release → operate; `test --verifies--> requirement` |
| **Processing** | which artifact types go through which processor and LLM backend | `*.py` → tree-sitter; `*.md` → frontmatter; `*.pdf` → LLM (`claude-cli`) |

Shipped packs (`src/workgraph/domains/`): **`sdlc`**, **`research`** (question → literature → hypothesis → experiment → analysis → write-up, where code is an experiment) and **`publishing`** (idea → outline → draft → edit → publish → promote). A new domain is a new YAML file. A workspace can mix domains, since each project picks its own pack, and all of them land in one graph.

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
- **Cross-project links**: byte-identical files get `identical_to` and same-path files that differ get `variant_of`, so clones of a template across repos show up (`workgraph clones`).
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
 sources ──▶┌──────────────┐
 (per layer)│  Ingestors   │  code · git · markdown · notes · web
            └──────┬───────┘
                   ▼
   layer graph files (one per layer: repo · product · org · process)
   ┌─────────────┐   convert    ┌─────────────┐
   │ JSON        │◀────────────▶│ YAML        │  human review and audit,
   │ canonical   │  (lossless)  │ with        │  comments added here
   │ + validation│              │ comments    │
   └──────┬──────┘              └──────┬──────┘
          │          ┌─────────────────┴──┐
          │          │ SQLite             │  file registry + comment store,
          │          │                    │  restores comments on JSON -> YAML
          │          └────────────────────┘
          ▼
   ┌──────────────┐
   │ Loader       │  resolves graph and sub-graph references, checks
   │              │  referential integrity, merges layers
   └──────┬───────┘
          ▼
            ┌──────────────┐
            │ Knowledge    │  one in-memory graph, every node tagged
            │ Graph        │  with its layer
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

- `README-graph-vision.md` in the ZiNets repository: the graph-first vision across the ZiNets ecosystem (ConceptBook, Graph-Code, and this project) and why a declarative graph is the common intermediate representation
- [docs/GUIDE/SETUP.md](https://github.com/digital-duck/work-graph/blob/main/docs/GUIDE/SETUP.md): installation, `cc_cli` (subscription) and Bedrock setup, troubleshooting
- [docs/GUIDE/USER-GUIDE.md](https://github.com/digital-duck/work-graph/blob/main/docs/GUIDE/USER-GUIDE.md): getting started, workspace files, linking conventions, commands, customizing domain packs

## Quickstart

```bash
conda create -n graf python=3.11 pip -y && conda activate graf   # dedicated env, see docs/GUIDE/SETUP.md
python -m pip install -e ".[dev]"
workgraph doctor                                      # confirm you are in graf, not conda base

workgraph domains                                     # list domain packs
workgraph build -c examples/workspace.yaml            # build the graph, write ~/workgraph/out/graph.json
workgraph gaps --src https://github.com/digital-duck/work-graph   # any local folder or git URL, no workspace file
workgraph gaps  -c examples/workspace.yaml            # report gaps per domain rules
workgraph clones -c examples/workspace.yaml           # files shared across projects
workgraph check -c examples/workspace.yaml            # validate the org/product layer files and cross-graph references
workgraph convert examples/graphs/product.json        # JSON <-> YAML, YAML comments kept in the store
workgraph open --semantic --adapter cc_cli -c examples/acme-shop/workspace.yaml   # open work items via the LLM pass
python -m pytest
```

`examples/` holds a small SDLC project (`acme-shop`) and a research project (`rq-demo`) with deliberate gaps, plus a workspace that builds both into one graph. LLM processors are off unless you pass `--semantic`.

## LLM backends (home vs work)

All LLM calls go through one adapter, `src/workgraph/llm.py`. The backend is chosen in this order: explicit argument, then the `WORKGRAPH_BACKEND` environment variable, then the project's `backend:` in `workspace.yaml`, then the processor's `backend:` in the domain pack, then the default `cc_cli`.

| Backend | Billing | Notes |
|---|---|---|
| `cc_cli` (default) | **Claude Code subscription** | Our own, ported from SPL.py's adapter. Strips `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_USE_BEDROCK/VERTEX/FOUNDRY` and the nested-session guards from the environment, so `claude` always uses your stored login. Prompt via stdin, tools off, launch retries, distinct rate-limit error. |
| `claude-cli` | whatever `claude` picks up | graphify's built-in. Passes the environment through, so with `ANTHROPIC_API_KEY` set it **bills the API per token**. Prefer `cc_cli`. |
| `claude`, `gemini`, `openai`, `kimi` | API, per token | graphify's, need API keys |
| `bedrock` | your AWS account | graphify's |
| `ollama` | local | graphify's |

```bash
# home: subscription, nothing to set
workgraph build --semantic -c workspace.yaml

# work: AWS Bedrock, same workspace file, no YAML edits
python -m pip install -e ".[bedrock]"
export WORKGRAPH_BACKEND=bedrock AWS_PROFILE=<profile> AWS_REGION=<region>
export GRAPHIFY_BEDROCK_MODEL=<model-or-inference-profile-id>   # graphify's built-in default is an old model
workgraph llm -c workspace.yaml          # resolved backend, billing mode and readiness checks per processor
workgraph build --semantic -c workspace.yaml
```

`cc_cli` model and timeout: `WORKGRAPH_CC_MODEL` (default `claude-sonnet-5`) and `WORKGRAPH_CC_TIMEOUT` (default 1200 s). LLM results are cached by backend and prompt, so switching backends re-extracts once. A session or rate limit stops the LLM pass with a warning instead of failing the build; rerun later and the cached documents are skipped.

## Layout

```
src/workgraph/
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

Multi-graph modules (design in [docs/DEV/README-design.md](https://github.com/digital-duck/work-graph/blob/main/docs/DEV/README-design.md)):

```
src/workgraph/
  schema.py        # layers, kinds, relation rules, validation of layer files
  refs.py          # load layer files, expand /** sub-graph references, merge into the build graph
  convert.py       # JSON <-> YAML converters; harvests and re-attaches YAML comments
  store.py         # SQLite: graph-file registry, comment store, uid history, Claude Code session journal
  paths.py         # the home folder (~/workgraph or $WORKGRAPH_HOME) and its layout
  sources.py       # --src <URL or path>: ad-hoc workspaces, shallow clones into <home>/src
  tracking.py      # init / track: the default workspace
  syncing.py       # export/import tar.gz, sync through a private git repo, privacy guards
  claude_import.py # Claude Code memory processor and import-claude
  claude_code.py   # Claude Code hooks: workgraph as the memory of your sessions
  query.py         # load, keyword search, semantic search (ollama / sentence-transformers / TF-IDF), neighbourhoods
  ui/app.py        # Streamlit app (`workgraph ui`)
  data/            # packaged copy of the /workgraph command
plugin/            # Claude Code plugin: hooks + the /workgraph command (not published; draft marketplace file in docs/DEV/)
examples/graphs/   # org and product layer files (JSON canonical, YAML for review)
```

## Hackathon scope

**Must have (MVP)**
- [x] Domain packs (`sdlc`, `research`, `publishing`) with constrained stage tags
- [x] Ingest code (tree-sitter) and Markdown with frontmatter; annotations; derived `verifies` edges
- [x] Gap analysis and broken-reference reporting
- [x] Persist the graph (`graph.json`) and load it back for search, `show`, `status` and the UI
- [ ] `ask` command: seed → expand → LLM answer with cited nodes
- [x] Streamlit app to review and query the graph (`workgraph ui`): overview, keyword and semantic search, browse, gaps, a node's links and neighbourhood
- [ ] Interactive visualization of the whole graph
- [x] Graph references: namespaced IDs, resolved in memory at load time, with broken-reference reporting across graphs
- [x] Product layer (thin): a repo `belongs_to` a product, and a product `follows` a process template

**Should have**
- [x] Dual formats: JSON (canonical, schema and referential-integrity validation) and YAML (review and audit), with converters both ways
- [x] Comment store: SQLite registry of graph files that saves YAML comments by anchor and restores them on JSON -> YAML
- [x] Sub-graph addressing (a node plus its descendants) for querying and export
- [x] Process layer: reusable process templates (such as stakeholder interview through feedback), separate from per-product instances
- [ ] Git history ingestion (commits ↔ files ↔ issues)
- [ ] LLM-proposed edges with accept/reject review
- [ ] Traceability and coverage-gap reports (requirement → code → test)
- [ ] Impact analysis ("what depends on this?")

**Stretch**
- [x] Org layer (company > division > department > group > team > person), kept optional and separable because of privacy
- [ ] Watch mode: incrementally update the graph as files change
- [x] Claude Code integration: hooks (session memory), the `/workgraph` command and a plugin
- [ ] MCP server so assistants can call search and show as tools
- [x] Ingest existing Claude Code memory and history (`import-claude`, memory nodes)
- [x] Sync a workgraph between machines through a private GitHub repository, with a tar.gz for first setup; the database is never shared
- [ ] Team or enterprise mode (a shared server)
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
- [graphify](https://github.com/Graphify-Labs/graphify), the engine under this project
- The ZiNets graph-first vision: `README-graph-vision.md` in the ZiNets repository

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](https://github.com/digital-duck/work-graph/blob/main/LICENSE).
