"""Phase 8 #2: the ROADMAP self-consistency check.

Asserts the real ROADMAP.md is consistent (every phase's `Progress: x/N` matches its checkbox
counts) — this test runs in CI, so a merge that drifts Progress fails. Plus unit tests of the parser
(mismatch / missing-progress / trailing-backlog-not-absorbed) and the CLI exit codes."""
import importlib.util
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
_SCRIPT = ROOT / "tools" / "check_roadmap.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_roadmap", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


check_roadmap = _load().check_roadmap


# --- the real file (the CI gate) ---
def test_real_roadmap_is_consistent():
    problems = check_roadmap((ROOT / "ROADMAP.md").read_text(encoding="utf-8"))
    assert problems == [], "ROADMAP Progress lines drifted:\n" + "\n".join(problems)


# --- parser units ---
def test_consistent_phase_passes():
    text = "## Phase 1 — x\n- [x] a\n- [ ] b\n\nProgress: 1/2\n"
    assert check_roadmap(text) == []


def test_count_mismatch_flagged():
    text = "## Phase 1 — x\n- [x] a\n- [ ] b\n\nProgress: 2/2\n"   # says 2 checked, only 1 is
    out = check_roadmap(text)
    assert len(out) == 1 and "2/2" in out[0] and "1 checked of 2" in out[0]


def test_total_mismatch_flagged():
    text = "## Phase 1 — x\n- [x] a\n- [ ] b\n\nProgress: 1/3\n"   # says 3 boxes, only 2
    assert "1 checked of 2" in check_roadmap(text)[0]


def test_missing_progress_flagged():
    assert "no 'Progress" in check_roadmap("## Phase 1 — x\n- [x] a\n")[0]


def test_trailing_backlog_not_absorbed_into_last_phase():
    # the Backlog boxes must NOT count toward Phase 1 (section ends at the next heading)
    text = ("## Phase 1 — x\n- [x] a\n\nProgress: 1/1\n\n"
            "## Backlog\n- [ ] later1\n- [ ] later2\n")
    assert check_roadmap(text) == []


def test_no_phases_flagged():
    assert check_roadmap("# Title\nsome text\n") == ["no '## Phase' sections found"]


def test_fenced_code_block_boxes_are_ignored():
    # an example checkbox inside a ``` fence must NOT count toward the phase's totals
    text = ("## Phase 1 — x\n- [x] real\n\nProgress: 1/1\n\n"
            "```\n- [ ] example in a code block\n- [x] another example\n```\n")
    assert check_roadmap(text) == []


def test_multiple_progress_lines_flagged():
    text = "## Phase 1 — x\n- [x] a\n\nProgress: 1/1\n\nProgress: 99/99\n"
    out = check_roadmap(text)
    assert len(out) == 1 and "expected exactly one" in out[0]


def test_phaseout_lookalike_heading_not_treated_as_phase():
    # '## Phaseout' is not a phase -> no Progress line demanded, no false 'no Progress' error
    assert check_roadmap("## Phaseout of scope\n- [ ] something\n") == ["no '## Phase' sections found"]


# --- CLI exit codes ---
def test_cli_passes_on_real_roadmap():
    r = subprocess.run([sys.executable, str(_SCRIPT), str(ROOT / "ROADMAP.md")],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "consistent" in r.stdout


def test_cli_fails_on_drifted_file(tmp_path):
    bad = tmp_path / "ROADMAP.md"
    bad.write_text("## Phase 1 — x\n- [x] a\n- [ ] b\n\nProgress: 2/2\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(_SCRIPT), str(bad)], capture_output=True, text=True)
    assert r.returncode == 1 and "FAILED" in r.stdout
