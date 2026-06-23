#!/usr/bin/env python3
"""Phase 8 #1: keep the GitHub Project board's phase Status in sync with the ROADMAP.

The board (NeuronEnix/homing-trade "homing-trade roadmap", project #2) mirrors the roadmap as one
issue card per phase, each carrying that phase's task checklist in its body. The card Status drifts
as phases complete; this tool derives each phase's status from its `Progress: x/N` line — Done when
x==N (all boxes ticked), In Progress when 0<x<N, Todo when x==0 — and updates only the cards whose
status is wrong, so the board always AGREES with the ROADMAP (the goal of Phase 8).

`python tools/sync_board.py [--apply]` — dry-run by default (prints the plan); `--apply` writes via
`gh project item-edit`. The parsing (phase_statuses / plan_updates) is pure + unit-tested; only the
gh layer touches the network.
"""
import json
import pathlib
import re
import subprocess
import sys

OWNER = "NeuronEnix"
PROJECT_NUMBER = "2"

_HEADING = re.compile(r"^##\s+.*$", re.M)
_PHASE_TITLE = re.compile(r"##\s+(Phase\b.*)$")
_PROGRESS = re.compile(r"^Progress:\s*(\d+)\s*/\s*(\d+)", re.M)


def status_for(checked, total):
    """Map a phase's Progress counts to a board Status."""
    if total > 0 and checked >= total:
        return "Done"
    if checked > 0:
        return "In Progress"
    return "Todo"


def phase_statuses(text):
    """{full phase title (without '## '): Status} derived from each phase's Progress line."""
    headings = list(_HEADING.finditer(text))
    out = {}
    for m in headings:
        title_m = _PHASE_TITLE.match(m.group(0).strip())
        if not title_m:
            continue
        nxt = next((h.start() for h in headings if h.start() > m.start()), len(text))
        pm = _PROGRESS.search(text[m.start():nxt])
        if pm:
            out[title_m.group(1).strip()] = status_for(int(pm.group(1)), int(pm.group(2)))
    return out


def plan_updates(items, desired):
    """Given board items [{id,title,status}] and desired {title: status}, return the list of
    {id, title, from, to} whose status must change. Items not matching a phase are ignored."""
    updates = []
    for it in items:
        title = (it.get("title") or "").strip()
        want = desired.get(title)
        if want is not None and (it.get("status") or "") != want:
            updates.append({"id": it["id"], "title": title, "from": it.get("status") or "(none)",
                            "to": want})
    return updates


def unmatched_phases(items, desired):
    """ROADMAP phase titles with no matching board card — a rename/dash drift would silently
    under-sync, so the caller warns loudly instead of leaving the board wrong."""
    titles = {(it.get("title") or "").strip() for it in items}
    return sorted(t for t in desired if t not in titles)


def missing_options(updates, available_option_names):
    """Target Status names that the board's Status field doesn't offer — checked BEFORE any write so
    a missing option can't half-apply the batch then crash."""
    return sorted({u["to"] for u in updates} - set(available_option_names))


def _gh_json(args):
    out = subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    apply = "--apply" in args
    roadmap = pathlib.Path(__file__).resolve().parents[1] / "ROADMAP.md"
    desired = phase_statuses(roadmap.read_text(encoding="utf-8"))

    items_raw = _gh_json(["project", "item-list", PROJECT_NUMBER, "--owner", OWNER,
                          "--format", "json"]).get("items", [])
    items = [{"id": it["id"], "title": (it.get("title") or it.get("content", {}).get("title") or ""),
              "status": it.get("status") or ""} for it in items_raw]
    for missing in unmatched_phases(items, desired):
        print(f"WARNING: ROADMAP phase has no matching board card (renamed?): {missing!r}")
    updates = plan_updates(items, desired)

    if not updates:
        print("Board already in sync with the ROADMAP.")
        return 0

    # Resolve the Status single-select field + option ids, and VALIDATE before any write (so dry-run
    # checks the same preconditions apply needs — no half-applied batch on a missing option).
    fields = _gh_json(["project", "field-list", PROJECT_NUMBER, "--owner", OWNER,
                       "--format", "json"]).get("fields", [])
    status_field = next((f for f in fields if f.get("name") == "Status"), None)
    opt = {o["name"]: o["id"] for o in (status_field or {}).get("options", [])}
    miss = missing_options(updates, opt)
    if status_field is None or miss:
        print(f"ERROR: board Status field unusable — {('no Status field' if status_field is None else 'missing options: ' + ', '.join(miss))}")
        return 1
    project_id = _gh_json(["project", "view", PROJECT_NUMBER, "--owner", OWNER,
                           "--format", "json"]).get("id")

    print(f"{'APPLY' if apply else 'DRY-RUN'} — {len(updates)} card(s) to update:")
    for u in updates:
        print(f"  {u['title'][:50]:<50} {u['from']} -> {u['to']}")
        if apply:
            subprocess.run(["gh", "project", "item-edit", "--id", u["id"],
                            "--project-id", project_id, "--field-id", status_field["id"],
                            "--single-select-option-id", opt[u["to"]]], check=True,
                           capture_output=True, text=True)
    if not apply:
        print("Re-run with --apply to write these changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
