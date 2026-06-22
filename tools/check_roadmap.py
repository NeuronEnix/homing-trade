#!/usr/bin/env python3
"""Phase 8 #2: ROADMAP self-consistency check.

Every `## Phase` section's `Progress: x/N` line must agree with its checkboxes: x == the number of
checked `- [x]` boxes, N == the total number of boxes. Run in CI (via tests/test_roadmap_consistency)
so a merge that ticks a box, adds a task, or edits Progress without keeping them in sync fails loudly
— "keep Progress lines accurate on every merge", enforced mechanically.

Usage: `python tools/check_roadmap.py [ROADMAP.md]` — prints every mismatch and exits non-zero.
"""
import re
import sys

_HEADING = re.compile(r"^##\s+.*$", re.M)              # any level-2 heading bounds a section
_PROGRESS = re.compile(r"^Progress:\s*(\d+)\s*/\s*(\d+)", re.M)
_CHECKED = re.compile(r"^- \[x\]", re.M | re.I)        # '- [x]' / '- [X]'
_UNCHECKED = re.compile(r"^- \[ \]", re.M)             # '- [ ]'


def check_roadmap(text):
    """Return a list of human-readable problem strings (empty == consistent)."""
    problems = []
    headings = list(_HEADING.finditer(text))
    phases = [m for m in headings if m.group(0).strip().startswith("## Phase")]
    if not phases:
        return ["no '## Phase' sections found"]
    for m in headings:
        title = m.group(0).strip()
        if not title.startswith("## Phase"):
            continue
        # the section ends at the NEXT heading of any kind, so a trailing non-Phase section
        # (e.g. '## Backlog') is never absorbed into the last phase
        nxt = next((h.start() for h in headings if h.start() > m.start()), len(text))
        section = text[m.start():nxt]
        checked = len(_CHECKED.findall(section))
        total = checked + len(_UNCHECKED.findall(section))
        pm = _PROGRESS.search(section)
        if not pm:
            problems.append(f"{title}: no 'Progress: x/N' line")
            continue
        px, pn = int(pm.group(1)), int(pm.group(2))
        if px != checked or pn != total:
            problems.append(
                f"{title}: Progress says {px}/{pn} but the boxes are {checked} checked of {total}")
    return problems


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    path = args[0] if args else "ROADMAP.md"
    with open(path, encoding="utf-8") as f:
        problems = check_roadmap(f.read())
    if problems:
        print("ROADMAP consistency FAILED:")
        for p in problems:
            print("  - " + p)
        return 1
    print("ROADMAP consistent: every phase's Progress matches its checkbox counts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
