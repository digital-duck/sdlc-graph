"""Console progress and timing for builds. Progress goes to stderr, so stdout still carries only results."""
from __future__ import annotations

import sys
import time
from contextlib import contextmanager


def fmt_secs(s: float) -> str:
    if s < 60:
        return f"{s:.1f}s"
    m, sec = divmod(int(round(s)), 60)
    return f"{m}m {sec:02d}s"


class NullProgress:
    """Does nothing: the default for library use and tests."""
    enabled = False

    def begin(self, n_projects: int, semantic: bool = False) -> None: ...
    def project_start(self, i: int, n: int, name: str) -> None: ...
    def note(self, text: str) -> None: ...
    def project_done(self, i: int, n: int, name: str, seconds: float, steps: list, nodes: int) -> None: ...
    def add(self, key: str, seconds: float) -> None: ...
    def finish_line(self) -> None: ...
    def table(self) -> str:
        return ""

    @contextmanager
    def phase(self, label: str, key: str | None = None):
        yield


class Progress(NullProgress):
    enabled = True

    def __init__(self, stream=None):
        self.out = stream or sys.stderr
        self.totals: dict[str, float] = {}
        self.started = time.perf_counter()

    def _p(self, text: str) -> None:
        print(text, file=self.out, flush=True)

    def begin(self, n_projects: int, semantic: bool = False) -> None:
        self.started = time.perf_counter()
        self._p(f"Building {n_projects} project{'s' if n_projects != 1 else ''}"
                + (" (LLM pass on: this can take minutes)" if semantic else "") + " ...")

    def project_start(self, i: int, n: int, name: str) -> None:
        self._p(f"[{i}/{n}] {name} ...")

    def note(self, text: str) -> None:
        self._p(f"      {text}")

    def project_done(self, i: int, n: int, name: str, seconds: float, steps: list, nodes: int) -> None:
        for label, secs, _ in steps:
            self.add(label, secs)
        slow = ", ".join(f"{label} {fmt_secs(secs)}" for label, secs, _ in steps if secs >= 0.5)
        self._p(f"[{i}/{n}] {name}  {nodes:,} nodes  {fmt_secs(seconds)}" + (f"   ({slow})" if slow else ""))

    def add(self, key: str, seconds: float) -> None:
        self.totals[key] = self.totals.get(key, 0.0) + seconds

    @contextmanager
    def phase(self, label: str, key: str | None = None):
        """A timed step. On a terminal a "label ..." marker shows while it runs and is replaced when it ends;
        steps under half a second leave no line behind. Output that is piped gets only the finished lines."""
        tty = hasattr(self.out, "isatty") and self.out.isatty()
        if tty:
            print(f"      {label} ...", end="", file=self.out, flush=True)
        t = time.perf_counter()
        try:
            yield
        finally:
            dt = time.perf_counter() - t
            self.add(key or label, dt)
            if tty:
                print("\r\033[K", end="", file=self.out)
            if dt >= 0.5:
                self._p(f"      {label}  {fmt_secs(dt)}")

    def elapsed(self) -> float:
        return time.perf_counter() - self.started

    def finish_line(self) -> None:
        self._p(f"Built in {fmt_secs(self.elapsed())}.")

    def table(self) -> str:
        """Where the time went, largest first, for the end of a build."""
        rows = sorted(self.totals.items(), key=lambda kv: -kv[1])
        lines = [f"timing: {fmt_secs(self.elapsed())} total"]
        lines += [f"  {k:<26} {fmt_secs(v):>8}" for k, v in rows if v >= 0.05]
        return "\n".join(lines)


_current: NullProgress = NullProgress()


def current() -> NullProgress:
    return _current


def set_current(p: NullProgress) -> None:
    global _current
    _current = p
