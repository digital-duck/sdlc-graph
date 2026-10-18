"""Query the built graph: load, summarize, keyword search, semantic search. No UI and no LLM here."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import networkx as nx

from . import paths

HIDDEN_KINDS = {"symbol", "external", "rationale"}      # structure, hidden from search results by default
EXCERPT_KINDS = {"document", "requirement", "decision", "task", "file", "memory"}
EXCERPT_CHARS = 1500
TOKEN_RE = re.compile(r"[a-z0-9]+")


def load_graph(path: str | Path | None = None) -> nx.MultiDiGraph:
    p = Path(path) if path else paths.out_dir() / "graph.json"
    if not p.exists():
        raise FileNotFoundError(f"no graph at {p}: run `workgraph build` first")
    return nx.node_link_graph(json.loads(p.read_text(encoding="utf-8")), edges="edges")


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.casefold().replace("_", " "))


@dataclass
class Doc:
    id: str
    label: str
    kind: str
    layer: str
    project: str
    stages: list[str]
    source_file: str
    summary: str
    body: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        """What gets searched and embedded."""
        parts = [self.label, self.kind, " ".join(self.stages), self.project, self.source_file, self.summary, self.body]
        return "\n".join(p for p in parts if p)


def build_docs(M: nx.MultiDiGraph, roots: dict[str, Path] | None = None, *, include_structure: bool = False) -> list[Doc]:
    """One searchable document per node. `roots` maps project name -> folder, used to add a file excerpt."""
    docs = []
    for n, d in M.nodes(data=True):
        kind = d.get("kind", "")
        if kind in HIDDEN_KINDS and not include_structure:
            continue
        body = ""
        root = (roots or {}).get(d.get("project", ""))
        if root and kind in EXCERPT_KINDS and d.get("source_file"):
            f = root / d["source_file"]
            try:
                if f.is_file():
                    body = f.read_text(encoding="utf-8", errors="replace")[:EXCERPT_CHARS]
            except OSError:
                pass
        docs.append(Doc(n, str(d.get("label", n)), kind, d.get("layer", "repo"), d.get("project", ""),
                        list(d.get("stages") or []), d.get("source_file") or "", str(d.get("summary") or ""), body))
    return docs


def roots_from_workspace(ws) -> dict[str, Path]:
    return {p.name: p.path for p in ws.projects} if ws else {}


def filter_docs(docs: list[Doc], *, layer=None, project=None, kind=None, stage=None) -> list[Doc]:
    return [d for d in docs if (not layer or d.layer == layer) and (not project or d.project == project)
            and (not kind or d.kind == kind) and (not stage or stage in d.stages)]


# --- keyword search

FIELD_WEIGHTS = (("label", 3.0), ("id", 2.0), ("source_file", 2.0), ("kind", 1.5), ("project", 1.0),
                 ("summary", 1.5), ("body", 1.0))


def _field_tokens(d: Doc) -> list[tuple[float, list[str]]]:
    vals = {"label": d.label, "id": d.id, "source_file": d.source_file, "kind": d.kind + " " + " ".join(d.stages),
            "project": d.project, "summary": d.summary, "body": d.body}
    return [(w, tokens(vals[f])) for f, w in FIELD_WEIGHTS]


def keyword_search(docs: list[Doc], query: str, limit: int = 25) -> list[tuple[float, Doc]]:
    """Ranked keyword match. Every query word counts (prefix matches included); documents matching more words,
    in more important fields (label > path > summary > body), rank higher. Words in "quotes" must appear in order."""
    phrases = re.findall(r'"([^"]+)"', query)
    words = tokens(re.sub(r'"[^"]+"', " ", query))
    if not words and not phrases:
        return []
    df: dict[str, int] = {}
    prepared = []
    for d in docs:
        ft = _field_tokens(d)
        prepared.append((d, ft))
        seen = {t for _, toks in ft for t in toks}
        for w in words:
            if any(t.startswith(w) for t in seen):
                df[w] = df.get(w, 0) + 1
    n = max(len(docs), 1)
    out = []
    for d, ft in prepared:
        if any(p.casefold() not in d.text.casefold() for p in phrases):
            continue
        fields = {"label": d.label, "id": d.id, "source_file": d.source_file, "kind": d.kind, "project": d.project,
                  "summary": d.summary, "body": d.body}
        score = sum(max(w for f, w in FIELD_WEIGHTS if p.casefold() in fields[f].casefold()) * 2 for p in phrases)
        matched = 0
        for w in words:
            idf = math.log(1 + n / (1 + df.get(w, 0)))
            best = 0.0
            for weight, toks in ft:
                hits = sum(1 for t in toks if t == w) + 0.5 * sum(1 for t in toks if t != w and t.startswith(w))
                if hits:
                    best = max(best, weight * (1 + math.log(hits)))
            if best:
                matched += 1
                score += best * idf
        if words and not matched and not phrases:
            continue
        out.append((score * ((matched / len(words)) if words and matched else 1.0), d))
    return sorted(out, key=lambda x: (-x[0], x[1].label))[:limit]


# --- semantic search

def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _ollama_url() -> str:
    return os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/") if os.environ.get(
        "OLLAMA_HOST", "").startswith("http") else "http://localhost:11434"


def embed_model() -> str:
    return os.environ.get("WORKGRAPH_EMBED_MODEL", "nomic-embed-text")


def _ollama_post(path: str, body: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(_ollama_url() + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def ollama_ready() -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(_ollama_url() + "/api/tags", timeout=3) as r:
            names = [m["name"] for m in json.loads(r.read()).get("models", [])]
    except (OSError, ValueError, urllib.error.URLError):
        return False, "ollama is not running"
    m = embed_model()
    if not any(n == m or n.startswith(m + ":") for n in names):
        return False, f"ollama has no embedding model {m!r} (run `ollama pull {m}`)"
    return True, m


def sbert_ready() -> tuple[bool, str]:
    try:
        import sentence_transformers  # noqa: F401
        return True, "all-MiniLM-L6-v2"
    except Exception as e:                      # missing, or broken numpy/torch build
        return False, f"sentence-transformers unavailable ({type(e).__name__})"


def available_backends() -> dict[str, tuple[bool, str]]:
    return {"ollama": ollama_ready(), "sbert": sbert_ready(), "tfidf": (True, "built in, lexical")}


def _cache_file(backend: str, model: str) -> Path:
    d = paths.cache_dir() / "embeddings"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{backend}-{re.sub(r'[^A-Za-z0-9._-]', '_', model)}.json"


def _embed_cached(texts: list[str], backend: str, model: str, embed_batch) -> list[list[float]]:
    """Embed `texts`, reusing vectors cached by content hash so only new or changed nodes cost anything."""
    cf = _cache_file(backend, model)
    cache = json.loads(cf.read_text()) if cf.exists() else {}
    keys = [hashlib.sha1(t.encode()).hexdigest() for t in texts]
    todo = [i for i, k in enumerate(keys) if k not in cache]
    for s in range(0, len(todo), 32):
        batch = todo[s:s + 32]
        for i, vec in zip(batch, embed_batch([texts[i][:2000] for i in batch])):
            cache[keys[i]] = vec
    if todo:
        cf.write_text(json.dumps(cache))
    return [cache[k] for k in keys]


def _tfidf_rank(docs: list[Doc], query: str) -> list[float]:
    """Pure-python TF-IDF cosine similarity. Lexical (shared words), not meaning, but needs nothing installed."""
    stem = lambda t: t[:-1] if len(t) > 3 and t.endswith("s") else t  # noqa: E731
    toks = [[stem(t) for t in tokens(d.text)] for d in docs]
    df: dict[str, int] = {}
    for ts in toks:
        for t in set(ts):
            df[t] = df.get(t, 0) + 1
    n = len(docs)
    idf = lambda t: math.log(1 + n / (1 + df.get(t, 0)))  # noqa: E731

    def vec(ts: list[str]) -> dict[str, float]:
        tf: dict[str, float] = {}
        for t in ts:
            tf[t] = tf.get(t, 0) + 1
        return {t: (1 + math.log(c)) * idf(t) for t, c in tf.items()}

    q = vec([stem(t) for t in tokens(query)])
    qn = math.sqrt(sum(v * v for v in q.values())) or 1.0
    scores = []
    for ts in toks:
        v = vec(ts)
        vn = math.sqrt(sum(x * x for x in v.values())) or 1.0
        scores.append(sum(q[t] * v.get(t, 0.0) for t in q) / (qn * vn))
    return scores


def semantic_search(docs: list[Doc], query: str, limit: int = 25, backend: str = "auto"
                    ) -> tuple[list[tuple[float, Doc]], str, str]:
    """Rank by similarity of meaning. Returns (results, backend used, note).
    `auto` tries ollama embeddings, then sentence-transformers, then falls back to lexical TF-IDF (and says so)."""
    order = ["ollama", "sbert", "tfidf"] if backend == "auto" else [backend]
    notes = []
    for b in order:
        if b not in ("ollama", "sbert", "tfidf"):
            raise ValueError(f"unknown semantic backend {b!r}; use auto, ollama, sbert or tfidf")
        if b == "tfidf":
            scores = _tfidf_rank(docs, query)
            note = "; ".join(notes + ["lexical similarity (TF-IDF), not embeddings"]) if notes or backend == "auto" \
                else "lexical similarity (TF-IDF), not embeddings"
            break
        ok, detail = ollama_ready() if b == "ollama" else sbert_ready()
        if not ok:
            if backend != "auto":
                raise RuntimeError(detail)
            notes.append(detail)
            continue
        texts = [d.text for d in docs]
        if b == "ollama":
            model = detail
            embed = lambda batch: _ollama_post("/api/embed", {"model": model, "input": batch})["embeddings"]  # noqa: E731
        else:
            from sentence_transformers import SentenceTransformer
            model = detail
            st = SentenceTransformer(model)
            embed = lambda batch: st.encode(batch).tolist()  # noqa: E731
        vecs = _embed_cached(texts, b, model, embed)
        qv = embed([query])[0]
        scores = [_cosine(qv, v) for v in vecs]
        note = f"embeddings via {b} ({model})"
        break
    ranked = sorted(zip(scores, docs), key=lambda x: -x[0])
    return [(s, d) for s, d in ranked if s > 0][:limit], b, note


# --- summary and neighbourhood

def summary(M: nx.MultiDiGraph) -> dict:
    layers: dict[str, int] = {}
    projects: dict[str, int] = {}
    kinds: dict[str, int] = {}
    for _, d in M.nodes(data=True):
        layers[d.get("layer", "repo")] = layers.get(d.get("layer", "repo"), 0) + 1
        k = d.get("kind", "")
        if k not in HIDDEN_KINDS and k not in ("stage", "project"):
            kinds[k] = kinds.get(k, 0) + 1
            if d.get("project"):
                projects[d["project"]] = projects.get(d["project"], 0) + 1
    return {"nodes": M.number_of_nodes(), "edges": M.number_of_edges(), "layers": layers, "projects": projects, "kinds": kinds}


def gaps_for(M: nx.MultiDiGraph) -> list[dict]:
    """Gap rules evaluated on a loaded graph, using the domain packs the nodes name."""
    from .analyze import find_gaps
    from .domain import Domain
    names = {d.get("domain") for _, d in M.nodes(data=True) if d.get("domain")}
    domains = {}
    for n in names:
        try:
            domains[n] = Domain.load(n)
        except ValueError:
            pass
    return find_gaps(M, SimpleNamespace(domains=domains))


def neighbors(M: nx.MultiDiGraph, node: str) -> dict[str, list[tuple[str, str, str]]]:
    """{'out': [(relation, other id, other label)], 'in': [...]} for one node."""
    lab = lambda i: str(M.nodes[i].get("label", i))  # noqa: E731
    out = [(d.get("relation", ""), v, lab(v)) for _, v, d in M.out_edges(node, data=True)]
    inn = [(d.get("relation", ""), u, lab(u)) for u, _, d in M.in_edges(node, data=True)]
    key = lambda x: (x[0], x[2])  # noqa: E731
    return {"out": sorted(out, key=key), "in": sorted(inn, key=key)}


def to_dot(M: nx.MultiDiGraph, node: str, limit: int = 40) -> str:
    """Graphviz DOT of a node and its direct neighbours (capped), for display."""
    esc = lambda s: str(s).replace('"', "'")[:48]  # noqa: E731
    nb = neighbors(M, node)
    lines = ["digraph G {", "rankdir=LR;", 'node [shape=box, style=rounded, fontsize=11];',
             f'"{esc(node)}" [label="{esc(M.nodes[node].get("label", node))}", style="rounded,filled", fillcolor="#cfe8ff"];']
    for rel, other, label in nb["out"][:limit]:
        lines += [f'"{esc(other)}" [label="{esc(label)}"];', f'"{esc(node)}" -> "{esc(other)}" [label="{esc(rel)}", fontsize=9];']
    for rel, other, label in nb["in"][:limit]:
        lines += [f'"{esc(other)}" [label="{esc(label)}"];', f'"{esc(other)}" -> "{esc(node)}" [label="{esc(rel)}", fontsize=9];']
    return "\n".join(lines + ["}"])
