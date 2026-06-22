"""Guard against the LLM 'profit mirage' (Phase 7 #6).

Two ways a backtest lies about edge:
  1. it runs on data from BEFORE the model's knowledge cutoff, so an LLM-driven strategy can lean on
     memorized outcomes (a mirage of skill that won't repeat live);
  2. it is in-sample rather than walk-forward, so it reports a fit, not a forecast.

This module makes both untrustworthy BY CONSTRUCTION: an evaluation is TRUSTED only when its window
lies entirely AFTER the trust cutoff AND it was produced walk-forward (out-of-sample). Everything
else is labelled untrusted so the promotion gate (Phase 7 #5) and the walk-forward harness can refuse
to act on it. The honest consequence is that only post-cutoff history counts as real evidence.
"""
from datetime import datetime, timezone


def cutoff_ms_from_iso(iso):
    """Parse an ISO-8601 UTC date/datetime ('2026-01-01' or '2026-01-01T00:00:00Z') to epoch ms.
    A BLANK value returns None (cutoff intentionally disabled). A NON-blank but unparseable value
    RAISES — failing CLOSED, so an operator typo can never silently disable the mirage guard."""
    if iso is None or not str(iso).strip():
        return None                      # intentionally unset -> no constraint
    try:
        dt = datetime.fromisoformat(str(iso).strip().replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"invalid trust cutoff {iso!r}: {e}. Refusing to silently disable the "
                         "profit-mirage guard — set '' to disable it intentionally.") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def is_post_cutoff(window_start_ms, cutoff_ms):
    """True if the whole evaluation window is after the cutoff. A None cutoff imposes no constraint;
    a None/unknown window start is treated as NOT post-cutoff (untrusted) once a cutoff is set."""
    if cutoff_ms is None:
        return True
    if window_start_ms is None:
        return False
    return window_start_ms >= cutoff_ms


def assess_window(start_ms, end_ms, *, cutoff_ms, walk_forward=True):
    """Classify an evaluation over [start_ms, end_ms]. Returns {trusted, post_cutoff, walk_forward,
    reason}. `trusted` requires walk-forward AND the entire window post-cutoff AND a well-formed
    window (end >= start) — a malformed window is untrusted, never silently trusted."""
    well_formed = start_ms is not None and end_ms is not None and end_ms >= start_ms
    post_cutoff = well_formed and is_post_cutoff(start_ms, cutoff_ms)
    trusted = bool(post_cutoff and walk_forward)
    if not well_formed:
        reason = "malformed evaluation window (end before start) — untrusted"
    elif not walk_forward:
        reason = "in-sample (not walk-forward) — untrusted"
    elif not post_cutoff:
        reason = "evaluation window predates the trust cutoff — untrusted (possible profit mirage)"
    else:
        reason = "trusted: walk-forward and entirely post-cutoff"
    return {"trusted": trusted, "post_cutoff": post_cutoff, "walk_forward": walk_forward,
            "reason": reason}


def partition_folds_by_trust(folds, cutoff_ms):
    """Split walk-forward folds (each carrying a `test_range` (start_ms, end_ms)) into
    (trusted, untrusted) by whether the test window is entirely post-cutoff. Folds are already
    walk-forward, so trust here == post-cutoff. A fold missing test_range is treated as untrusted."""
    trusted, untrusted = [], []
    for f in folds:
        tr = f.get("test_range") if isinstance(f, dict) else None
        ok = bool(tr) and is_post_cutoff(tr[0], cutoff_ms)
        (trusted if ok else untrusted).append(f)
    return trusted, untrusted
