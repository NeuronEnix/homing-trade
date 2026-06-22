"""Phase-4 #8: confidence calibration -> a proposed confidence floor.

Reads the per-confidence-band realized win rate (SelfQuery.confidence_calibration — mechanical,
from prices, embargo-aware) and, when the low-confidence bands demonstrably underperform, FILES a
human-gated `param` proposal raising the confidence floor to the lowest band that clears the
target win rate. It only PROPOSES (never applies), is idempotent against an identical pending
proposal, and emits nothing when there is no actionable signal (too few samples, every band
already clears, or nothing clears so a floor wouldn't help). No LLM — purely mechanical.
"""
import json

from homing_trade.selfquery import SelfQuery


def propose_confidence_floor(repo, strategy, now_ms, *, target_win_rate=0.5, min_band_n=5,
                             starting_balance=5000.0, source_reflection_id=None):
    """File a confidence-floor proposal if calibration warrants it; return its id, else None."""
    cal = SelfQuery(repo, starting_balance).confidence_calibration(strategy, as_of=now_ms)
    sized = [b for b in cal if b["n"] >= min_band_n]          # only bands with enough evidence
    if not sized:
        return None
    failing_low = [b for b in sized if b["win_rate"] < target_win_rate]
    clearing = [b for b in sized if b["win_rate"] >= target_win_rate]
    if not failing_low or not clearing:
        return None                                          # nothing to fix, or nothing clears
    floor = min(b["lo"] for b in clearing)                   # lowest confidence that performs
    if not any(b["lo"] < floor for b in failing_low):
        return None                                          # the floor wouldn't exclude a loser
    # Idempotent: if ANY confidence-floor proposal is already pending for this strategy, wait for
    # the human to act on it rather than stacking another (a drifting floor would otherwise
    # accumulate distinct pending rows on the reflection cadence).
    for p in repo.pending_proposals(strategy):
        if p["kind"] == "param":
            try:
                if "confidence_floor" in json.loads(p["payload_json"]):
                    return None
            except Exception:
                pass
    rationale = (f"Confidence calibration: trades entered below {floor:.2f} confidence realized "
                 f"win rate < {target_win_rate:.0%}; raise the confidence floor to {floor:.2f}.")
    return repo.create_proposal(strategy, "param", {"confidence_floor": floor}, rationale, now_ms,
                                source_reflection_id=source_reflection_id)
