# User Guide

How to get started and use `work-graph`. Installation is covered in [SETUP.md](SETUP.md).

## What it does

`work-graph` reads your projects (code, markdown notes, planning docs) and builds one **knowledge graph** of everything in them. Each artifact is tagged with the lifecycle **stages** it belongs to (requirement, design, build, test, ...), and links between artifacts (`satisfies`, `implements`, `verifies`, ...) are recorded. Then it answers questions the files alone cannot:

- Which requirements have no design or no tests?
- Which code files are never tested?
- What work items are still open in my roadmap?
- Which files are copies of each other across my repos?

The structural build needs no LLM. An optional LLM pass (`--semantic`) additionally reads planning prose and extracts work items.

## Core concepts

| Concept | Meaning |
|---|---|
| **Workspace** | A `workspace.yaml` listing your projects (folders or repos). |
| **Domain pack** | A YAML file defining a domain: its stages, the legal links between them, the gap rules, path rules, and which processor handles which file type. Shipped: `sdlc`, `research`, `publishing`. |
| **Stage tags** | Each artifact carries one or more stage tags from the pack's fixed vocabulary, for example `[requirement, design]`. Unknown tags are rejected. |
| **Kind** | What an artifact is: `file`, `document`, `requirement`, `decision`, `task`, `symbol`, `external`, ... |
| **Confidence** | `EXTRACTED` (explicit in the source), `INFERRED` (derived or LLM-suggested), `AMBIGUOUS`. |
| **Gap** | An artifact missing a link its domain pack expects, such as a requirement with no test. |

The graph has two layers. The **process layer** is the stages and projects (`stage:sdlc:requirement` ...). The **knowledge layer** is your artifacts. Analysis runs on the knowledge layer only.

## Quick start

All commands below run from the repo root with the environment activated (see SETUP.md).

### 1. Build the bundled example

```bash
work-graph build -c examples/workspace.yaml
```

```
37 nodes, 91 edges, 8 communities
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
warning: docs/adr/ADR-002.md: broken reference `satisfies: REQ-099`
wrote work-graph-out/graph.json
```

This is one graph with two domains: `acme-shop` uses the `sdlc` pack, `rq-demo` uses the `research` pack. The table shows how many artifacts each project has in each stage.

### 2. See the gaps

```bash
work-graph gaps -c examples/workspace.yaml
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
work-graph llm  -c examples/acme-shop/workspace.yaml --adapter cc_cli
work-graph open -c examples/acme-shop/workspace.yaml --semantic --adapter cc_cli
```

```
acme-shop:docs/ROADMAP.md (4 open)
  - [requirement/requirement,test] Checkout: pay for an order
  - [requirement/requirement,design] Discount codes
  - [decision/design] Decide between Stripe and Adyen for payments
  - [task/build] Add a nightly job that expires abandoned carts
```

The LLM read `docs/ROADMAP.md`, extracted the unchecked items, and tagged each with kind and stages. Run it again and it finishes instantly: results are cached.

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
work-graph build -c workspace.yaml
work-graph gaps  -c workspace.yaml
work-graph clones -c workspace.yaml
```

**3. Add the LLM pass** when you want work items:

```bash
work-graph build --semantic --adapter cc_cli -c workspace.yaml
work-graph open  --semantic --adapter cc_cli -c workspace.yaml
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
| `work-graph domains` | list the shipped domain packs and their stages |
| `work-graph build` | build the graph, print a per-stage summary, write `work-graph-out/graph.json` |
| `work-graph gaps` | report artifacts missing links that their pack expects, plus broken references |
| `work-graph clones` | list files shared across projects (`=` identical, `~` same path but different) |
| `work-graph open` | list open work items extracted by the LLM pass (use with `--semantic`) |
| `work-graph llm` | show the resolved LLM backend per processor, its billing mode, and readiness |

Options (not every option applies to every command; `work-graph <command> --help` shows the exact set):

| Option | Meaning |
|---|---|
| `-c`, `--config FILE` | workspace file (default `workspace.yaml`) |
| `--semantic` | also run the LLM processors |
| `--adapter NAME` | LLM backend: `cc_cli` (subscription), `bedrock` (AWS), or any graphify backend |
| `-o`, `--out DIR` | output folder (default `work-graph-out`) |

### Choosing the adapter

`--adapter` wins over everything. The full order is: `--adapter`, then the `WORK_GRAPH_BACKEND` environment variable, then the project's `backend:`, then the pack's `backend:`, then the default `cc_cli`.

```bash
# home: subscription
work-graph build --semantic --adapter cc_cli -c workspace.yaml

# work: AWS Bedrock (see SETUP.md, section 6)
work-graph build --semantic --adapter bedrock -c workspace.yaml
```

Always run `work-graph llm --adapter <name>` first if you are unsure: it shows the billing mode and what is missing.

### What the LLM pass does and costs

`--semantic` runs the pack's LLM processors. For the `sdlc` pack, `doc-items` reads planning and design documents (files named like `*roadmap*.md`, `*todo*.md`, `*plan*.md`, `design*.md`) and extracts requirements, decisions and tasks. Each becomes a node with:

- `origin: llm` and `INFERRED` confidence
- `status: done | open | unknown` as the document states it
- stage tags from the pack's vocabulary (anything else the model returns is dropped)
- a `derived_from` link to its source document

LLM items are not checked by gap rules until you confirm them, because otherwise every extracted requirement would show up as a gap. Documents are sent one at a time, capped at about 24,000 characters each, with at most 40 items per document. If your subscription limit is reached, the pass stops with a warning, the build still completes, and a rerun skips everything already cached (`work-graph-out/cache/`).

## Reading the output

`work-graph-out/graph.json` is a standard NetworkX node-link file. Load it in Python:

```python
import json
import networkx as nx

data = json.load(open("work-graph-out/graph.json"))
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

Packs live in `src/work_graph/domains/*.yaml`. To adapt the `sdlc` pack, edit or copy it. A copy can be used by path:

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
| Not sure which backend or billing applies | `work-graph llm -c workspace.yaml --adapter <name>`. |
