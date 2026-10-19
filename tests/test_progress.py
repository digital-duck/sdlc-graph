import io
import time
from pathlib import Path

from workgraph.cli import main
from workgraph.domain import Workspace
from workgraph.pipeline import build_workspace
from workgraph.progress import NullProgress, Progress, fmt_secs

EX = Path(__file__).parent.parent / "examples"


def test_fmt_secs():
    assert fmt_secs(0.04) == "0.0s" and fmt_secs(36.24) == "36.2s" and fmt_secs(65) == "1m 05s" and fmt_secs(3600) == "60m 00s"


def test_progress_reports_each_project_and_collects_timings():
    buf = io.StringIO()
    prog = Progress(buf)
    ws = Workspace.load(EX / "workspace.yaml")
    build_workspace(ws, progress=prog)
    out = buf.getvalue()
    assert "Building 2 projects" in out and "[1/2] acme-shop" in out and "[2/2] rq-demo" in out and " nodes " in out
    assert {"scan files", "code (tree-sitter)"} <= set(prog.totals)
    assert prog.totals["clustering"] >= 0 and "timing:" in prog.table()


def test_phase_is_silent_when_fast_and_timed_when_slow():
    buf = io.StringIO()
    prog = Progress(buf)
    with prog.phase("quick", "quick"):
        pass
    assert buf.getvalue() == ""                       # not a terminal and under half a second: no line
    with prog.phase("slow step", "slow"):
        time.sleep(0.55)
    assert "slow step" in buf.getvalue() and prog.totals["slow"] >= 0.5


def test_null_progress_prints_nothing_and_default_build_is_quiet(capsys):
    n = NullProgress()
    n.begin(1)
    with n.phase("x"):
        pass
    assert n.table() == ""
    build_workspace(Workspace.load(EX / "acme-shop" / "workspace.yaml"))
    cap = capsys.readouterr()
    assert cap.out == "" and "Building" not in cap.err


def test_cli_build_shows_progress_on_stderr_and_timing_on_stdout(capsys):
    main(["build", "-c", str(EX / "workspace.yaml")])
    cap = capsys.readouterr()
    assert "[1/2] acme-shop" in cap.err and "Building 2 projects" in cap.err
    assert "timing:" in cap.out and "wrote" in cap.out
    main(["build", "-c", str(EX / "workspace.yaml"), "--quiet"])
    cap = capsys.readouterr()
    assert cap.err == "" and "timing:" not in cap.out
