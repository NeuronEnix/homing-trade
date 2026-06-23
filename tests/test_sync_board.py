"""Phase 8 #1: the board status-sync derivation (pure parts).

The board (one issue card per phase) drifts; sync_board derives each phase's Status from the ROADMAP
`Progress: x/N` line and updates only the wrong cards. Tests the pure status mapping, the per-phase
parse against the real ROADMAP, and the dry-run/update planner. The gh layer is not exercised here."""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("sync_board", ROOT / "tools" / "sync_board.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sb = _load()


def test_status_for_mapping():
    assert sb.status_for(8, 8) == "Done"
    assert sb.status_for(3, 4) == "In Progress"
    assert sb.status_for(8, 9) == "In Progress"      # one box still open -> not Done
    assert sb.status_for(0, 4) == "Todo"
    assert sb.status_for(0, 0) == "Todo"             # no boxes yet


def test_phase_statuses_on_synthetic():
    text = ("## Phase 1 — a\n- [x] x\n\nProgress: 1/1\n\n"
            "## Phase 2 — b\n- [x] x\n- [ ] y\n\nProgress: 1/2\n\n"
            "## Phase 3 — c\n- [ ] x\n\nProgress: 0/1\n")
    s = sb.phase_statuses(text)
    assert s["Phase 1 — a"] == "Done"
    assert s["Phase 2 — b"] == "In Progress"
    assert s["Phase 3 — c"] == "Todo"


def test_phase_statuses_on_real_roadmap():
    s = sb.phase_statuses((ROOT / "ROADMAP.md").read_text(encoding="utf-8"))
    # spot-check the phases whose state we know: completed phases -> Done, Phase 8 in progress
    assert s["Phase 1 — Structural foundation (kill the god-files, harden module boundaries)"] == "Done"
    assert any(k.startswith("Phase 7") and v == "Done" for k, v in s.items())
    assert any(k.startswith("Phase 8") and v == "In Progress" for k, v in s.items())
    assert any(k.startswith("Phase 10") and v == "Todo" for k, v in s.items())


def test_plan_updates_only_changes_wrong_cards():
    desired = {"Phase 1 — a": "Done", "Phase 2 — b": "In Progress"}
    items = [
        {"id": "i1", "title": "Phase 1 — a", "status": "Todo"},        # wrong -> update
        {"id": "i2", "title": "Phase 2 — b", "status": "In Progress"}, # correct -> skip
        {"id": "i3", "title": "Unrelated card", "status": "Todo"},     # not a phase -> ignore
    ]
    plan = sb.plan_updates(items, desired)
    assert len(plan) == 1
    assert plan[0] == {"id": "i1", "title": "Phase 1 — a", "from": "Todo", "to": "Done"}


def test_plan_updates_empty_when_in_sync():
    desired = {"Phase 1 — a": "Done"}
    items = [{"id": "i1", "title": "Phase 1 — a", "status": "Done"}]
    assert sb.plan_updates(items, desired) == []


def test_unmatched_phases_flags_renamed_cards():
    desired = {"Phase 1 — a": "Done", "Phase 2 — b": "Todo"}
    items = [{"id": "i1", "title": "Phase 1 — a", "status": "Done"}]   # Phase 2 card missing/renamed
    assert sb.unmatched_phases(items, desired) == ["Phase 2 — b"]
    assert sb.unmatched_phases([{"id": "i1", "title": "Phase 1 — a"}], {"Phase 1 — a": "Done"}) == []


def test_missing_options_guards_partial_write():
    updates = [{"to": "Done"}, {"to": "In Progress"}, {"to": "Blocked"}]
    assert sb.missing_options(updates, ["Todo", "In Progress", "Done"]) == ["Blocked"]
    assert sb.missing_options([{"to": "Done"}], ["Todo", "In Progress", "Done"]) == []
