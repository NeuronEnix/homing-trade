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
_PHASE_TITLE = re.compile(r"##\s+Phase\b")             # '## Phase' as a word (not 'Phaseout')
_PROGRESS = re.compile(r"^Progress:\s*(\d+)\s*/\s*(\d+)", re.M)
_FENCE = re.compile(r"```.*?```", re.S)                # fenced code blocks (examples, not task boxes)
_CHECKED = re.compile(r"^- \[x\]", re.M | re.I)        # '- [x]' / '- [X]'
_UNCHECKED = re.compile(r"^- \[ \]", re.M)             # '- [ ]'


def check_roadmap(text):
    """Return a list of human-readable problem strings (empty == consistent). Section boundaries are
    level-2 headings; fenced code blocks are stripped so example checkboxes/Progress lines inside
    them never count; each phase must carry EXACTLY ONE Progress line matching its box counts."""
    problems = []
    headings = list(_HEADING.finditer(text))
    if not any(_PHASE_TITLE.match(m.group(0).strip()) for m in headings):
        return ["no '## Phase' sections found"]
    for m in headings:
        title = m.group(0).strip()
        if not _PHASE_TITLE.match(title):
            continue
        # the section ends at the NEXT heading of any kind, so a trailing non-Phase section
        # (e.g. '## Backlog') is never absorbed into the last phase
        nxt = next((h.start() for h in headings if h.start() > m.start()), len(text))
        section = _FENCE.sub("", text[m.start():nxt])   # ignore example boxes inside code fences
        checked = len(_CHECKED.findall(section))
        total = checked + len(_UNCHECKED.findall(section))
        progs = _PROGRESS.findall(section)
        if not progs:
            problems.append(f"{title}: no 'Progress: x/N' line")
            continue
        if len(progs) > 1:
            problems.append(f"{title}: {len(progs)} 'Progress:' lines (expected exactly one)")
            continue
        px, pn = int(progs[0][0]), int(progs[0][1])
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
