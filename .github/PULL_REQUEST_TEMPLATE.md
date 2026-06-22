<!-- The maintainer merges on trust without reading the diff line-by-line, so make this PR
     self-explanatory. Keep each section to a few lines. -->

## What & why
<!-- What this PR changes and the reason. Link the ROADMAP box (e.g. "Phase 8 #3"). -->

## What's tested
<!-- New/changed tests and how to run them. State the suite result (e.g. "Suite N green").
     CI runs `python -m pytest -q` on this PR — it must be green to merge. -->

## UI changes
<!-- Screenshots / a short description of any dashboard change, or "none". -->

## Safety
<!-- Required. Confirm the change does NOT touch the protected zones, or explain + flag for
     explicit human sign-off if it must. -->
- [ ] Does **NOT** touch risk limits / kill-switch / leverage / position sizing.
- [ ] Does **NOT** touch secrets, `.env`, or `data/` (no secrets or DBs committed).
- [ ] Does **NOT** arm live trading (`LiveBroker` stays `dry_run`; paper-only).
- [ ] Any self-modification / proposal path stays **human-gated** (nothing auto-applies).
