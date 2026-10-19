# User Guide

How to get started and use `workgraph`, a knowledge layer that sits beside Claude Code: it turns your projects, their lifecycle, products, teams and processes into one searchable graph, and gives your Claude Code sessions a persistent memory of it. Installation is covered in [SETUP.md](SETUP.md); a guided walk-through is in [TUTORIAL.md](TUTORIAL.md).

## What it does

`workgraph` reads your projects (code, markdown notes, planning docs) and builds one **knowledge graph** of everything in them. Each artifact is tagged with the lifecycle **stages** it belongs to (requirement, design, build, test, ...), and links between artifacts (`satisfies`, `implements`, `verifies`, ...) are recorded. Then it answers questions the files alone cannot:

- Which requirements have no design or no tests?
- Which code files are never tested?
- What work items are still open in my roadmap?
- Which files are copies of each other across my repos?

The structural build needs no LLM. An optional LLM pass (`--semantic`) additionally reads planning prose and extracts work items.

## Three ways to use it

| Way | For | Where it is described |
|---|---|---|
| **The CLI**, `workgraph` | building graphs from folders and repos, gap reports, search, validation, conversion | this guide |
| **Claude Code**, the plugin and `/workgraph` | making the graph the memory of your Claude Code sessions: projects are tracked automatically, the graph's summary is added to each session's context, and `/workgraph` queries it from inside a session | "Using workgraph as the memory of Claude Code" |
| **The web app**, `workgraph ui` (Streamlit) | reviewing and searching the graph in a browser: overview, keyword and semantic search, browsing, gaps, a node's links and neighbourhood | "Searching and reviewing the graph" |

All three read the same files in the home folder (`~/workgraph`). For a guided walk-through with real output, see [TUTORIAL.md](TUTORIAL.md).

## Core concepts

| Concept | Meaning |
|---|---|
| **Workspace** | A `workspace.yaml` listing your projects (folders or repos). |
| **Domain pack** | A YAML file defining a domain: its stages, the legal links between them, the gap rules, path rules, and which processor handles which file type. Shipped: `sdlc`, `research`, `publishing`. |
| **Stage tags** | Each artifact carries one or more stage tags from the pack's fixed vocabulary, for example `[requirement, design]`. Unknown tags are rejected. |
| **Kind** | What an artifact is: `file`, `document`, `requirement`, `decision`, `task`, `symbol`, `external`, ... |
| **Confidence** | `EXTRACTED` (explicit in the source), `INFERRED` (derived or LLM-suggested), `AMBIGUOUS`. |
| **Gap** | An artifact missing a link its domain pack expects, such as a requirement with no test. |
| **Layer** | One of several graphs that reference each other: `repo` (built from your projects), `product`, `org` and `process` (written as layer files). Every node has a `layer`. |
| **Layer file** | A JSON file (with a YAML twin for review) in `<home>/custom/graphs/` that defines a product, org or process layer. See "Layers". |
| **Home folder** | `~/workgraph` (or `$WORKGRAPH_HOME`): the default workspace, layer files, the built graph, caches and clones. |

Inside the repo layer, the **process nodes** are the stages and projects (`stage:sdlc:requirement` ...) and the **knowledge nodes** are your artifacts. Analysis runs on the knowledge nodes only. Layers are covered below.

## Quick start

All commands below run from the repo root with the environment activated (see SETUP.md).

### 1. Build the bundled example

```bash
workgraph build -c examples/workspace.yaml
```

```
57 nodes, 130 edges, 8 communities
[sdlc]
  requirement  {'acme-shop': 4}
  design       {'acme-shop': 2}
  build        {'acme-shop': 2}
  test         {'acme-shop': 1}
  ...
[research]
  question     {'rq-demo': 2}
  literature   {'rq-demo': 1}
  hypothesis   {'rq-demo': 1}
  experiment   {'rq-demo': 2}
  ...
layers: org 6, process 23, product 3, repo 25
warning: docs/adr/ADR-002.md: broken reference `satisfies: REQ-099`
wrote ~/workgraph/out/graph.json
```

This is one graph with two domains: `acme-shop` uses the `sdlc` pack, `rq-demo` uses the `research` pack. The table shows how many artifacts each project has in each stage. The `layers:` line shows that the example workspace also loaded the org, product and process layer files that sit next to it in `examples/graphs/`.

### 2. See the gaps

```bash
workgraph gaps -c examples/workspace.yaml
```

```
[sdlc] code file has no test (1)
  - checkout.py  (acme-shop:src/checkout.py)

[sdlc] requirement has no design (1)
  - REQ-003: Discount codes  (acme-shop:docs/requirements/REQ-003.md)

[sdlc] requirement has no test (2)
  - REQ-002: Customers can check out
  - REQ-003: Discount codes

[research] question has no hypothesis (1)
  - Q2: Is graph retrieval cheaper than chunk retrieval?
...
```

Covered items (REQ-001, `cart.py`, Q1, H1) are not listed. Broken references are listed under `[warnings]`.

### 3. Add the LLM pass with `cc_cli`

`cc_cli` uses your Claude Code subscription (SETUP.md, section 5). Check it first, then extract work items from the planning docs:

```bash
workgraph utils show-llm  -c examples/acme-shop/workspace.yaml --adapter cc_cli
workgraph open -c examples/acme-shop/workspace.yaml --semantic --adapter cc_cli
```

```
acme-shop:docs/ROADMAP.md (4 open)
  - [requirement/requirement,test] Checkout: pay for an order
  - [requirement/requirement,design] Discount codes
  - [decision/design] Decide between Stripe and Adyen for payments
  - [task/build] Add a nightly job that expires abandoned carts
```

The LLM read `docs/ROADMAP.md`, extracted the unchecked items, and tagged each with kind and stages. Run it again and it finishes instantly: results are cached.

### Ingest any folder or git URL, with no workspace file

```bash
workgraph build --src ~/projects/my-app
workgraph gaps  --src https://github.com/some-org/some-repo
workgraph build --src ~/projects/app-a --src ~/projects/app-b --domain sdlc
```

`--src` accepts a local folder or a git URL (`https://`, `ssh://`, `git://` or `git@host:path`) and can be repeated. URLs are shallow-cloned into `<home>/src/<name>` (and fast-forwarded on later runs); nothing from the clone is executed. Every `--src` project uses `--domain` (default `sdlc`). It cannot be combined with `-c`. The graph is written to `<home>/out/graph.json` like any other build, and `status`, `search` and `ui` then read it.

## Using it on your own projects

**1. Write a `workspace.yaml`** anywhere (paths are relative to the file, absolute paths also work):

```yaml
domain: sdlc                 # default pack for projects that do not say otherwise
projects:
  - {name: concept-book,    path: /home/me/projects/concept-book}
  - {name: conceptbook-app, path: /home/me/projects/conceptbook-app}
  - {name: my-experiments,  path: /home/me/projects/exp, domain: research}   # per-project pack
```

Per-project options:

| Key | Meaning |
|---|---|
| `name` | label used in output and node ids |
| `path` | project folder |
| `domain` | pack for this project (default: the file-level `domain`) |
| `backend` | LLM backend for this project (overridden by `--adapter`) |
| `ignore` | extra gitignore-style patterns to skip |

**2. Build, then look at gaps and clones:**

```bash
workgraph build -c workspace.yaml
workgraph gaps  -c workspace.yaml
workgraph utils clones -c workspace.yaml
```

**3. Add the LLM pass** when you want work items:

```bash
workgraph build --semantic --adapter cc_cli -c workspace.yaml
workgraph open  --semantic --adapter cc_cli -c workspace.yaml
```

### Ignoring noise

Generated files drown out real knowledge, and ingestion skips them when it can. It honors, in this order of addition:

1. `.graphifyignore` and `.gitignore` in the project
2. the pack's default `ignore:` (logs, lock files, `*.db`, `docs/research`, `docs/archive`, ...)
3. the `ignore:` list for that project in `workspace.yaml`

Committed run logs are the usual culprit, since `.gitignore` cannot exclude them. If a build reports thousands of nodes, find the noisy folder and add it:

```yaml
projects:
  - name: conceptbook-app
    path: /home/me/projects/conceptbook-app
    ignore: ["spl/cookbook/**", "**/generated/**"]
```

Files that are symlinks pointing outside the project are skipped automatically.

To keep a set of projects instead of naming them every time, track them. The default workspace in your home folder then needs no `-c`:

```bash
workgraph init                    # creates ~/workgraph and a starter workspace.yaml
workgraph track ~/projects/my-app # adds the folder (comments in workspace.yaml are kept)
workgraph build                   # builds every tracked project
```

Claude Code can do the tracking for you (see "Using workgraph as the memory of Claude Code").

## Layers: product, org and process

Besides the repo graph, workgraph holds graphs that describe the work around the code. Each is a **layer file** in `<home>/custom/graphs/` (or in a `graphs/` folder next to the workspace file you pass with `-c`; the bundled `examples/graphs/` has three):

| Layer | Node kinds | Contains |
|---|---|---|
| `product` | `family`, `product`, `application` | what you ship: family > product > application |
| `org` | `company`, `division`, `department`, `group`, `team`, `person` | who builds and owns it |
| `process` | `process`, `step` | a delivery process, such as stakeholder interview, requirement spec, design, prototype, build, test, release, feedback |

Every domain pack is also a process template, available as `process:<domain>` (for example `process:sdlc`) without a layer file.

A layer file (shown as YAML; the JSON twin has the same content):

```yaml
schema: workgraph/1
layer: product
nodes:
  - id: product:acme/retail/shop            # <layer>:<readable path>
    uid: b6976f29                           # short, permanent; only used to detect renames
    kind: product
    label: Shop
edges:
  - source: product:acme/retail/shop
    relation: owned_by                      # -> an org node
    target: org:acme/engineering/commerce/payments
  - source: project:acme-shop               # a repo project
    relation: belongs_to
    target: product:acme/retail/shop
  - source: product:acme/retail/shop
    relation: follows                       # -> a process
    target: process:sdlc
```

Relations between layers: `part_of` (child to parent, inside one layer), `precedes` (process step to the next step), `belongs_to` (repo project to product), `owned_by` (product to org), `follows` (product to process). Nodes are defined once and referenced by id from other files. An id ending in `/**` stands for a node and all its descendants, so `org:acme/engineering/**` as an edge source creates one edge per member.

At build time all layer files are loaded, every reference is resolved, and everything is merged into one graph with a `layer` on each node. Two commands help you author them:

```bash
workgraph utils check -c workspace.yaml      # validate the files and every reference
workgraph utils convert graphs/product.yaml  # YAML -> JSON (and the reverse for a .json file)
```

`check` reports invalid files, broken references (with a "renamed to ..." hint when the `uid` shows a node was renamed), YAML files edited since the last conversion, and nodes with no `uid` (`check --assign-uids` fills them in). The loader reads only the JSON files; YAML is for people. **JSON is canonical, YAML is for review**: edit the YAML, add comments, then run `convert`. Comments are saved in the database (`<home>/workgraph.sqlite3`) and put back whenever the YAML is regenerated from the JSON, so they are never lost. A comment on a node that was later deleted is kept at the end of the file as `# [orphaned from <id>] ...`.

## Making links explicit

The more explicit links you give it, the better the gap reports. Three ways:

**Frontmatter in markdown.** Any key named after a relation of the pack creates an edge:

```markdown
---
id: ADR-001
kind: decision
title: Keep carts in memory per session
satisfies: [REQ-001]
---
# Carts live in the session
```

Useful fields: `id` (how other files refer to it), `kind`, `title`, `stages` (extra stage tags), plus any relation: `satisfies`, `implements`, `verifies`, ... (see the pack for the full list).

**Comments in code.** A comment of the form `# relation: ID` in any code file links that file:

```python
# implements: ADR-001
class Cart: ...
```

```python
# verifies: REQ-001
def test_add(): ...
```

`//` and `--` comments work too. Targets can be document ids (`REQ-001`) or project-relative paths.

**Derived automatically.** A test file that imports a code file gets an `INFERRED` `verifies` edge to it. This rule comes from the pack's `flow_relations`.

A reference to something that does not exist (`REQ-099` above) is reported as a warning rather than silently dropped.

## Command reference

| Command | Purpose |
|---|---|
| `workgraph init` | create the home folder (`~/workgraph`) and a starter workspace |
| `workgraph track [PATH]` | add a project folder to the default workspace |
| `workgraph build` | build the graph, print a per-stage summary, write `<home>/out/graph.json` |
| `workgraph gaps` | report artifacts missing links that their pack expects, plus broken references |
| `workgraph open` | list open work items extracted by the LLM pass (use with `--semantic`) |
| `workgraph status` | summary of the last build: layers, projects, gaps |
| `workgraph search QUERY` | keyword search over the built graph; `--semantic` ranks by meaning (see below) |
| `workgraph show NODE` | one node's attributes and links (a node id, or a label such as `REQ-002`) |
| `workgraph ui` | open the Streamlit app to review and query the graph |
| `workgraph import-claude` | backfill from Claude Code: track the projects it has worked in and load its session metadata (dry run unless `--apply`) |
| `workgraph dump -o FILE` | pack your authored graph data into a tar.gz (a USB stick), without the database |
| `workgraph load FILE` | merge such a tar.gz into this machine |
| `workgraph sync init|push|pull|status` | share your workgraph between machines through a private git repository |
| `workgraph utils SUBCOMMAND` | helper commands, below |

**Helper commands**, `workgraph utils ...`:

| Subcommand | Purpose |
|---|---|
| `check` | validate the layer files (`custom/graphs/`) and every cross-graph reference |
| `convert FILE` | convert a layer file JSON <-> YAML; YAML comments are saved in and restored from the database |
| `claude-setup` | show, or with `--write` install, the Claude Code hooks and the `/workgraph` command (see below) |
| `clones` | list files shared across projects (`=` identical, `~` same path but different) |
| `context [PATH]` | what the graph knows about the project at PATH (what Claude Code gets at session start) |
| `doctor` | show which Python, environment, graphify and default backend are in use; warns if you are in conda base |
| `domains` | list the shipped domain packs and their stages |
| `show-llm` | show the resolved LLM backend per processor, its billing mode, and readiness |

There is also a hidden `workgraph hook EVENT` (`session-start`, `session-end`): the Claude Code hook handler. It is installed for you, never needs typing, and keeps its name so installed hooks keep working.

Options (not every option applies to every command; `workgraph <command> --help` shows the exact set):

| Option | Meaning |
|---|---|
| `--src URL_OR_PATH` | build from a local folder or a git URL (shallow-cloned into `<home>/src`) instead of a workspace file; repeat it for several. Not combinable with `-c` |
| `--domain NAME` | domain pack for `--src` projects (default `sdlc`) |
| `-c`, `--config FILE` | workspace file. Default: `./workspace.yaml`, else `<home>/workspace.yaml`. A relative name that is not in the current folder is also looked for in `<home>`, so `-c workspace.yaml` finds your default workspace from anywhere |
| `--semantic` | also run the LLM processors |
| `--adapter NAME` | LLM backend: `cc_cli` (subscription), `bedrock` (AWS), or any graphify backend |
| `-o`, `--out DIR` | output folder (default `<home>/out`) |
| `-q`, `--quiet` | no progress or timing output (also: set `WORKGRAPH_QUIET=1`) |

### Progress and timing

Commands that build the graph (`build`, `gaps`, `open`, `utils clones`) show progress on **stderr** while they run, so what you pipe or redirect (stdout) still holds only the results:

```
Building 3 projects ...
[1/3] conceptbook-app ...
[1/3] conceptbook-app  1,263 nodes  8.4s   (scan files 6.0s, code (tree-sitter) 2.2s)
[2/3] ...
```

One line when a project starts and one when it finishes, with its node count and time, and a breakdown of the steps that took half a second or more. Slow shared steps (assembling the graph, clustering, merging layer files) get their own line when they take more than half a second, and on a terminal a "... " marker shows while they run. With `--semantic` the LLM step says how many documents it will read and prints `LLM 2/4: <file>` before each call that is not already cached. `build` ends with a **timing table**, largest first, on stdout. Add `-q` to silence it all.

### The home folder

Data lives in `~/workgraph` (set `WORKGRAPH_HOME` to move it): the default `workspace.yaml`, `custom/` (everything you author yourself: your layer files in `custom/graphs/`), `workgraph.sqlite3` (comments, uid history, session journal), `out/graph.json`, `cache/`, `src/` (clones for `--src` URLs) and `sync/` (see "Moving to another machine"). `workgraph init` creates it. Because workgraph is installed as a package, nothing is written next to the code or into your projects. See SETUP.md, section 7.

### Searching and reviewing the graph

```bash
workgraph search "checkout test"                         # keyword: names, paths, summaries, file excerpts
workgraph search '"discount codes"'                      # quoted words must appear together
workgraph search "customers paying for an order" --semantic
workgraph search auth --layer repo --kind requirement    # filters: --layer --project --kind
workgraph show REQ-002
workgraph ui                                             # the same, in a browser
```

Keyword search ranks matches in a node's name above matches in its path, summary or the first 1,500 characters of its file, and matches partial words (`chec` finds `checkout`). **Semantic** search compares meaning. `--backend auto` tries, in order:

| Backend | Needs | Notes |
|---|---|---|
| `ollama` | a running Ollama with an embedding model: `ollama pull nomic-embed-text` (change it with `WORKGRAPH_EMBED_MODEL`) | local, free; vectors are cached in `<home>/cache/embeddings`, so only new or changed nodes are embedded |
| `sbert` | nothing extra: sentence-transformers is installed with workgraph | sentence-transformers, `all-MiniLM-L6-v2` (downloaded once from Hugging Face). Runs on the CPU by default; set `WORKGRAPH_EMBED_DEVICE=cuda` to use a GPU |
| `tfidf` | nothing | **lexical** (shared words), not meaning; used as the fallback, and the output says so |

`workgraph ui` is part of the normal install. It has an Overview (counts per layer, project and kind), Search (keyword or semantic, with the backend shown), Browse and Gaps tabs. Click a row to see a node's attributes, its links in and out, and a diagram of its neighbourhood. Sidebar filters (layer, project, kind, stage) apply everywhere, and *Reload* picks up a new build.

### Using workgraph as the memory of Claude Code

Two Claude Code hooks turn the graph into persistent memory for your sessions. Install them one of two ways (not both, or the hooks run twice):

```bash
# A. the plugin: hooks plus the command /workgraph:workgraph.
#    Not published to a marketplace yet (it is still being tested); to try it from a clone:
claude --plugin-dir <path to this repo>/plugin
```

```bash
# B. plain settings: hooks plus the shorter command /workgraph
workgraph utils claude-setup            # prints the settings.json snippet
workgraph utils claude-setup --write    # merges it into ~/.claude/settings.json (a .bak copy is kept) and adds ~/.claude/commands/workgraph.md
```

Plugin commands always carry the plugin name, which is why route A gives `/workgraph:workgraph` and route B gives `/workgraph`.

- **SessionStart**: the project you opened is added to the default workspace (`workgraph track` does the same by hand), the session is logged, and a short summary of what the last build knows is printed. Claude Code adds that output to the session's context: stage counts, gaps, open work items, the product and owner the project belongs to, and what earlier sessions changed.
- **SessionEnd**: the branch and changed files are recorded in the database (`workgraph.sqlite3`), and the graph is rebuilt in the background when files or the commit changed.

The hooks never block or fail a session, ignore your home folder, and only read your project files. The rebuild is the same structural `workgraph build` (no LLM, no subscription use), always of the default workspace in your home folder.

**The `/workgraph` command** works inside a session: `/workgraph status`, `/workgraph search <text>`, `/workgraph gaps`, `/workgraph show REQ-002`, `/workgraph build`, `/workgraph track`, or a free-form question, which it answers from the graph (semantic search, then `show` on the best hits). It runs only `workgraph` commands, and never starts the LLM passes, because those spend your Claude subscription.

### Importing the work Claude Code has already done

The hooks only see sessions that start after they are installed. To pick up what is already on the machine:

```bash
workgraph import-claude                      # dry run: shows what it found and what it would do
workgraph import-claude --apply --with-titles
workgraph build
```

It looks in `~/.claude/projects` (read-only), works out the real folder behind each entry, and for each one decides: *track* it, it is *already tracked*, or *skip* it (the folder is gone, it is your home or `/tmp`, or it sits inside another project, such as generated output; `--include-nested` overrides the last). `--apply` adds the folders to your default workspace and loads one journal row per Claude Code session: when, which branch, which files were edited, and with `--with-titles` Claude's own one-line title. **Message text is never stored.**

Claude Code's memory files for a tracked project are read on every build and appear as nodes of kind `memory` (`workgraph search "..." --kind memory`). They are searchable, tagged with no lifecycle stage so gap rules ignore them, and stay on the machine: they are not part of sync.

### Moving to another machine

Use a **private** GitHub repository for ongoing sync, and a tar.gz for the first setup. Do not put the home folder in Google Drive, iCloud or Dropbox.

```bash
# machine A, once: a USB stick
workgraph dump -o workgraph.tar.gz
# machine B
workgraph load workgraph.tar.gz

# both machines: ongoing, through a private repository
workgraph sync init git@github.com:you/my-workgraph.git     # refuses a public repository
workgraph sync push                                          # export, commit, push
workgraph sync pull --clone-missing                          # pull, merge, clone projects not yet here
workgraph sync status
```

What travels is the part that cannot be rebuilt, as text: the portable workspace (project names and repository URLs, not local folders), the layer files, and your YAML comments and uid history. What never travels: the database, the built graph, caches and project clones. The **org layer** (it names people), the **session journal** (it lists file names) and the **LLM cache** are opt-in: `--with org,sessions,llm-cache` on `export` or `sync init`.

After an import or pull, a project may have no folder on this machine. `workgraph track <folder>` binds it, or `sync pull --clone-missing` clones it into `~/workgraph/src`. Then run `workgraph build` to regenerate the graph.

Safety rules the tool applies for you: the database lives outside every synced folder; the repository's `.gitignore` allows only the files workgraph writes; every export and push is scanned and stops on a database file, a file over 5 MB or something that looks like an API key or private key; an archive is unpacked only if it contains plain files under one folder. If a layer file changed on both machines, yours is kept and the other is saved beside it as `<name>.incoming.<ext>` to merge by hand. A comment you deleted on one machine can come back from the other.

### Choosing the adapter

`--adapter` wins over everything. The full order is: `--adapter`, then the `WORKGRAPH_BACKEND` environment variable, then the project's `backend:`, then the pack's `backend:`, then the default `cc_cli`.

```bash
# home: subscription
workgraph build --semantic --adapter cc_cli -c workspace.yaml

# work: AWS Bedrock (see SETUP.md, section 6)
workgraph build --semantic --adapter bedrock -c workspace.yaml
```

Always run `workgraph utils show-llm --adapter <name>` first if you are unsure: it shows the billing mode and what is missing.

### What the LLM pass does and costs

`--semantic` runs the pack's LLM processors. For the `sdlc` pack, `doc-items` reads planning and design documents (files named like `*roadmap*.md`, `*todo*.md`, `*plan*.md`, `design*.md`) and extracts requirements, decisions and tasks. Each becomes a node with:

- `origin: llm` and `INFERRED` confidence
- `status: done | open | unknown` as the document states it
- stage tags from the pack's vocabulary (anything else the model returns is dropped)
- a `derived_from` link to its source document

LLM items are not checked by gap rules until you confirm them, because otherwise every extracted requirement would show up as a gap. Documents are sent one at a time, capped at about 24,000 characters each, with at most 40 items per document. If your subscription limit is reached, the pass stops with a warning, the build still completes, and a rerun skips everything already cached (`<home>/cache/`).

## Reading the output

`<home>/out/graph.json` (default `~/workgraph/out/graph.json`) is a standard NetworkX node-link file. Load it in Python:

```python
import json
import networkx as nx

data = json.load(open("/home/you/workgraph/out/graph.json"))
G = nx.node_link_graph(data, edges="edges")      # a MultiDiGraph

# every requirement and what satisfies it
for n, d in G.nodes(data=True):
    if d.get("kind") == "requirement":
        satisfied_by = [u for u, _, e in G.in_edges(n, data=True) if e["relation"] == "satisfies"]
        print(d["label"], "<-", [G.nodes[u]["label"] for u in satisfied_by])

# everything INFERRED (derived or from the LLM), to review
inferred = [(u, v, e["relation"]) for u, v, e in G.edges(data=True) if e["confidence"] == "INFERRED"]
```

Useful node attributes: `kind`, `label`, `project`, `domain`, `stages` (list), `source_file`, and for LLM items `origin`, `status`, `summary`. Useful edge attributes: `relation`, `confidence`, `source_file`.

## Customizing a domain pack

Built-in packs live in `src/workgraph/domains/*.yaml`. Your own go in `<home>/custom/domains/<name>.yaml` and are
used by name (`domain: my-process`); `workgraph utils domains` lists them marked `(custom)`. A custom pack with the
same name as a built-in one replaces it. To start, copy a built-in pack there and edit it. A pack anywhere else can
be used by path:

```yaml
projects:
  - {name: mine, path: ., domain: /home/me/packs/my-sdlc.yaml}
```

The sections of a pack:

| Section | Controls |
|---|---|
| `stages` | the stage vocabulary and its flow (`next`) |
| `flow_relations` | legal links between stages; also the names usable in frontmatter and code comments |
| `expectations` | the gap rules: "artifacts of these kinds in this stage need an incoming/outgoing edge of this relation" |
| `classifiers` | path globs that assign stage tags (case-insensitive; every matching rule adds a tag; `default: true` applies when none matched) |
| `ignore` | default noise patterns |
| `processors` | which file types go through which processor: `ast` (code), `frontmatter` (markdown), `annotations` (code comments), `llm_items` and `llm` (need `--semantic`) |

Example: treat `docs/specs/` as requirements and require every requirement to have an owner link:

```yaml
classifiers:
  - {stage: requirement, globs: ["**/docs/specs/**"]}
expectations:
  - {stage: requirement, kinds: [requirement], needs: owned_by, direction: in, message: "requirement has no owner"}
flow_relations:
  - {from: design, to: requirement, relation: satisfies}
```

An expectation can also set `min_symbols` (skip trivial files) and `exclude` (path globs to skip). Run `pytest` after changing a pack; the loader rejects references to unknown stages.

## Domains beyond SDLC

The same engine works for other domains by switching the pack: `research` (question, literature, hypothesis, experiment, analysis, write-up; code lives in the experiment stage) and `publishing` (idea to promote). Mixed workspaces work: each project picks its own pack, and they land in one graph. `examples/workspace.yaml` is a working mixed example.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Everything is tagged `build` | Your paths do not match the pack's classifiers. Add globs under `classifiers`, or name folders like `docs/design/`, `tests/`. |
| No requirements or designs appear | Plain markdown becomes `document` nodes. Add frontmatter with `kind: requirement`, or use `--semantic` on planning docs. |
| Thousands of nodes | Generated files. See "Ignoring noise". |
| `gaps` lists every code file | The project has no tests that import the code. Real signal, or add `# verifies:` annotations. |
| `limit reached` during `--semantic` | Subscription limit. Rerun later; cached documents are not repeated. |
| A reference warning for an id that exists | The id is case-insensitive but must match the `id:` frontmatter value after normalization (`REQ-001` and `req_001` are the same). Check for typos and that the file has frontmatter. |
| Not sure which backend or billing applies | `workgraph utils show-llm -c workspace.yaml --adapter <name>`. |
| `workgraph ui` shows an error mentioning `_ARRAY_API` or `numpy.core.multiarray` | A NumPy 2 with a pyarrow built for NumPy 1, usually in conda *base*. Use the dedicated environment from SETUP.md. |
| `search --semantic` says it used TF-IDF | No embedding backend was available, so the ranking is lexical. Start Ollama and `ollama pull nomic-embed-text`, or check that sentence-transformers imports in your environment (`python -c "import sentence_transformers"`). |
| `check` says `X.yaml: edited since the last convert` | The loader reads JSON only. Run `workgraph utils convert X.yaml`. |
| `sync init` says it cannot verify the repository is private | It checks GitHub through the `gh` CLI. Install and log in to `gh`, or confirm yourself with `--confirm-private` after checking. A public repository is always refused. |
| `sync` or `dump` stops with "refusing to export" | The scan found a database, a large file or something shaped like a key. Remove it from the listed file; nothing was written to the repository. |
| After a pull, `unresolved: <project>` | That project has no folder here. `workgraph track <folder>` or `workgraph sync pull --clone-missing`. |
| A `.incoming.json` file appeared in `custom/graphs/` | The layer file changed on both machines. Merge the two by hand, delete the `.incoming` copy, run `workgraph utils check`. |
| `/workgraph` is not found in Claude Code | Plugin installs give `/workgraph:workgraph`. For the plain name run `workgraph utils claude-setup --write`. |
| `claude -p` fails with a model error | `ANTHROPIC_MODEL` points at a retired model. Unset it or set a current one. workgraph's own LLM calls use `WORKGRAPH_CC_MODEL`, not that variable. |
| The graph looks stale after working in Claude Code | The session-end rebuild runs in the background; see `<home>/out/build.log`, or run `workgraph build`. |
