"""Domain packs and workspaces.

A domain pack (domains/<name>.yaml) is the only thing that changes between use cases:
vocabulary (stages, relations, gap rules, path classifiers) plus processing (which
artifact types go through which processor and LLM backend). The graph engine is shared.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

import yaml

DOMAINS_DIR = Path(__file__).parent / "domains"
# Node kinds that are structure, not knowledge: excluded from gap checks and summaries.
STRUCTURAL_KINDS = {"stage", "project", "rationale", "symbol", "external"}


def make_id(s: str) -> str:
    """Same shape as graphify's node ids: lowercase, [a-z0-9_] runs."""
    return re.sub(r"[^\w]+", "_", s.casefold()).strip("_")


def glob_match(path: str, globs: list[str]) -> bool:
    """Case-insensitive glob match (README.md vs readme.md); a leading `**/` also matches the root."""
    path = path.casefold()
    return any(fnmatchcase(path, g.casefold()) or fnmatchcase("/" + path, g.casefold()) for g in globs)


def available_domains() -> list[str]:
    return sorted(p.stem for p in DOMAINS_DIR.glob("*.yaml"))


@dataclass
class Stage:
    id: str
    label: str
    next: list[str] = field(default_factory=list)


@dataclass
class Processor:
    name: str
    use: str
    match: list[str] = field(default_factory=list)
    backend: str | None = None
    kinds: list[str] = field(default_factory=list)   # item kinds for the llm_items processor


@dataclass
class Domain:
    name: str
    label: str
    stages: dict[str, Stage]
    flow_relations: list[dict]
    expectations: list[dict]
    classifiers: list[dict]
    processors: list[Processor]
    ignore: list[str] = field(default_factory=list)   # default noise patterns for this domain

    @classmethod
    def load(cls, name_or_path: str | Path) -> "Domain":
        p = Path(name_or_path)
        if p.suffix not in (".yaml", ".yml"):
            p = DOMAINS_DIR / f"{name_or_path}.yaml"
            if not p.exists():
                raise ValueError(f"unknown domain {name_or_path!r}; available: {available_domains()}")
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        stages = {s["id"]: Stage(s["id"], s.get("label", s["id"]), s.get("next", [])) for s in raw["stages"]}
        d = cls(
            name=raw["domain"], label=raw.get("label", raw["domain"]), stages=stages,
            flow_relations=raw.get("flow_relations", []), expectations=raw.get("expectations", []),
            classifiers=raw.get("classifiers", []), ignore=raw.get("ignore", []),
            processors=[Processor(x["name"], x["use"], x.get("match", []), x.get("backend"), x.get("kinds", []))
                        for x in raw.get("processors", [])],
        )
        d._check()
        return d

    def _check(self) -> None:
        def need(stage: str, where: str) -> None:
            if stage not in self.stages:
                raise ValueError(f"[{self.name}] {where} references unknown stage {stage!r}")
        for s in self.stages.values():
            for n in s.next:
                need(n, f"stage {s.id}.next")
        for r in self.flow_relations:
            need(r["from"], "flow_relations"); need(r["to"], "flow_relations")
        for e in self.expectations:
            need(e["stage"], "expectations")
        for c in self.classifiers:
            need(c["stage"], "classifiers")

    @property
    def relation_names(self) -> set[str]:
        return {r["relation"] for r in self.flow_relations}

    def stage_node(self, stage: str) -> str:
        return f"stage:{self.name}:{stage}"

    def classify(self, rel_path: str) -> list[str]:
        """Stage tags for a project-relative path: every matching rule adds one, else the default."""
        tags, default = [], None
        for rule in self.classifiers:
            if rule.get("default"):
                default = rule["stage"]
            elif glob_match(rel_path, rule["globs"]):
                tags.append(rule["stage"])
        return list(dict.fromkeys(tags or ([default] if default else [])))

    def validate_stages(self, tags: list[str]) -> list[str]:
        """Enforce the constrained tag vocabulary; returns de-duplicated tags in order."""
        unknown = [t for t in tags if t not in self.stages]
        if unknown:
            raise ValueError(f"unknown stage tag(s) {unknown} for domain {self.name!r}; allowed: {sorted(self.stages)}")
        return list(dict.fromkeys(tags))

    def process_extraction(self) -> dict:
        """The process layer (stages + `precedes` flow) as a graphify extraction dict."""
        nodes, edges = [], []
        for s in self.stages.values():
            nodes.append({"id": self.stage_node(s.id), "label": s.label, "file_type": "concept",
                          "source_file": f"domains/{self.name}.yaml", "kind": "stage",
                          "domain": self.name, "stages": [s.id]})
            for n in s.next:
                edges.append({"source": self.stage_node(s.id), "target": self.stage_node(n), "relation": "precedes",
                              "confidence": "EXTRACTED", "source_file": f"domains/{self.name}.yaml"})
        return {"nodes": nodes, "edges": edges, "hyperedges": []}


@dataclass
class Project:
    name: str
    path: Path
    domain: Domain
    backend: str | None = None   # overrides the LLM backend named in the domain's processors
    ignore: list[str] = field(default_factory=list)   # extra gitignore-style patterns (generated output etc.)


@dataclass
class Workspace:
    projects: list[Project]
    graphs_dir: Path | None = None   # layer files (product, org, process), see docs/DEV/README-design.md
    unresolved: list[str] = field(default_factory=list)   # projects with no folder on this machine (synced, not yet cloned/tracked)

    @property
    def domains(self) -> dict[str, Domain]:
        return {p.domain.name: p.domain for p in self.projects}

    @classmethod
    def load(cls, path: str | Path) -> "Workspace":
        path = Path(path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        default = raw.get("domain", "sdlc")
        cache: dict[str, Domain] = {}
        projects, unresolved = [], []
        for p in raw["projects"]:
            dn = p.get("domain", default)
            cache.setdefault(dn, Domain.load(dn))
            if p.get("path"):
                folder = (path.parent / p["path"]).resolve()
            else:                      # a project that arrived through sync: usable once cloned or tracked here
                from . import paths
                folder = paths.src_dir() / p["name"]
                if not folder.is_dir():
                    unresolved.append(p["name"])
                    continue
            projects.append(Project(p["name"], folder, cache[dn],
                                    p.get("backend", raw.get("backend")), p.get("ignore", [])))
        graphs = path.parent / raw["graphs"] if raw.get("graphs") else path.parent / "graphs"
        return cls(projects, graphs.resolve() if graphs.is_dir() else None, unresolved)
