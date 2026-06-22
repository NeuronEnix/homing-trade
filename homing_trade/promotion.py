"""Promotion discipline (Phase 7 #5).

A variant is promoted ONLY when, out-of-sample, it (1) beats the baseline, (2) by at least the
pre-registered minimum-detectable-effect, and (3) at a significance that survives multiple-comparison
correction over the search budget (how many variants were tried). Even then it is never auto-enabled:
the gate FILES a human-gated `strategy_toggle` proposal — the same approval chokepoint everything else
uses. This is the guard against the LLM 'profit mirage' and against p-hacking by trying many variants.

Layers:
  * bonferroni_alpha / benjamini_hochberg — the multiple-comparison corrections;
  * promotion_decision — the PURE gate (won OOS ∧ clears MDE ∧ significant-after-correction);
  * evaluate_and_maybe_promote — orchestrates: run the OOS two-sample test, conclude the experiment
    row, and file a promotion proposal iff the gate passes. Convention: variant_a is the CHALLENGER
    (the candidate being promoted), variant_b is the BASELINE/incumbent.
"""
import json

from homing_trade.experiments import two_sample_test

DEFAULT_ALPHA = 0.05
DEFAULT_MIN_SAMPLES = 30        # per variant; below this the OOS evidence is too thin to promote
_DAY_MS = 86_400_000
DEFAULT_FAMILY_WINDOW_MS = 90 * _DAY_MS   # search-budget family: experiments started in the last 90d
DEFAULT_REJECT_COOLDOWN_MS = 7 * _DAY_MS  # don't re-nag a promotion a human just rejected


def bonferroni_alpha(alpha, n_tests):
    """Per-test significance after Bonferroni family-wise correction: alpha / max(n_tests, 1)."""
    return alpha / max(int(n_tests), 1)


def benjamini_hochberg(pvals, alpha):
    """Benjamini–Hochberg step-up (controls FDR at alpha). Returns a reject-mask aligned to `pvals`
    plus the largest passing p-threshold (0.0 if none reject)."""
    m = len(pvals)
    if m == 0:
        return [], 0.0
    order = sorted(range(m), key=lambda i: pvals[i])
    kmax = 0
    for rank, i in enumerate(order, start=1):
        if pvals[i] <= rank / m * alpha:
            kmax = rank
    threshold = (kmax / m * alpha) if kmax else 0.0
    reject = [False] * m
    for rank, i in enumerate(order, start=1):
        if rank <= kmax:
            reject[i] = True
    return reject, threshold


def promotion_decision(*, diff, mde, p_value, n_a, n_b, alpha=DEFAULT_ALPHA, search_budget=1,
                       min_samples=DEFAULT_MIN_SAMPLES):
    """The pure promotion gate. `diff` = mean(challenger) − mean(baseline); `p_value` is the TWO-sided
    test p. Returns {promote, won_oos, clears_mde, significant, enough_samples, corrected_alpha,
    p_one_sided, reason}.

    The hypothesis is directional (challenger BEATS baseline), so we use the one-sided p
    (two_sided/2 when diff>0) — this neither over- nor under-spends alpha. Significance is then judged
    against a Bonferroni correction over the search budget. All four conditions must hold to promote;
    the reason explains the first failing one."""
    corrected_alpha = bonferroni_alpha(alpha, search_budget)
    enough_samples = n_a >= min_samples and n_b >= min_samples
    won_oos = diff > 0
    clears_mde = mde is not None and diff >= mde
    p_one_sided = p_value / 2.0 if diff > 0 else 1.0 - p_value / 2.0   # directional test
    significant = p_one_sided < corrected_alpha
    promote = enough_samples and won_oos and clears_mde and significant
    if not enough_samples:
        reason = f"insufficient OOS samples (n_a={n_a}, n_b={n_b} < {min_samples})"
    elif not won_oos:
        reason = f"challenger did not beat baseline out-of-sample (diff={diff:.4f} <= 0)"
    elif not clears_mde:
        reason = (f"effect {diff:.4f} below the pre-registered MDE "
                  f"{'(unset)' if mde is None else f'{mde:.4f}'}")
    elif not significant:
        reason = (f"one-sided p={p_one_sided:.4g} not significant after Bonferroni over "
                  f"{search_budget} tests (corrected alpha={corrected_alpha:.4g})")
    else:
        reason = (f"promote: challenger beats baseline by {diff:.4f} (>= MDE), one-sided "
                  f"p={p_one_sided:.4g} < corrected alpha {corrected_alpha:.4g} over {search_budget} tests")
    return {"promote": promote, "won_oos": won_oos, "clears_mde": clears_mde,
            "significant": significant, "enough_samples": enough_samples,
            "corrected_alpha": corrected_alpha, "p_one_sided": p_one_sided, "reason": reason}


def _is_enable_toggle(p, challenger):
    """Is proposal `p` a strategy_toggle enabling `challenger`?"""
    if p.get("kind") != "strategy_toggle":
        return False
    try:
        payload = json.loads(p["payload_json"])
    except Exception:
        return False
    return isinstance(payload, dict) and payload.get("strategy") == challenger \
        and payload.get("action") == "enable"


def _promotion_blocked(repo, challenger, now_ms, cooldown_ms):
    """Block a (re-)filing if an identical enable-`challenger` promotion is already PENDING, or was
    REJECTED within `cooldown_ms` (don't stack duplicates; don't re-nag a just-rejected one).
    Returns (blocked, reason). Scans by payload — the challenger lives in the payload, not the
    strategy column (proposals are filed with strategy=None, mirroring research.py)."""
    for p in repo.pending_proposals():
        if _is_enable_toggle(p, challenger):
            return True, "promotion proposal already pending"
    for p in repo.recent_proposals():
        if p.get("status") == "rejected" and _is_enable_toggle(p, challenger):
            dt = p.get("decided_ts")
            if dt is not None and (now_ms - dt) < cooldown_ms:
                return True, "identical promotion was rejected recently (cooldown)"
    return False, ""


def evaluate_and_maybe_promote(repo, experiment_id, challenger_samples, baseline_samples, now_ms,
                               *, alpha=DEFAULT_ALPHA, min_samples=DEFAULT_MIN_SAMPLES,
                               family_window=None, family_window_ms=DEFAULT_FAMILY_WINDOW_MS,
                               reject_cooldown_ms=DEFAULT_REJECT_COOLDOWN_MS):
    """Conclude experiment `experiment_id` from its OOS samples and, iff the gate passes, file a
    human-gated promotion proposal. Idempotent + crash-safe: a concluded experiment is not
    re-evaluated; a duplicate pending (or recently-rejected) promotion is not re-filed; and the
    proposal is filed BEFORE the experiment is concluded, so a crash in between leaves the experiment
    'running' and a re-run completes it without double-filing. Returns the verdict (+ proposal_id).

    The search-budget family (the Bonferroni denominator) is experiments STARTED in the last
    `family_window_ms` (a rolling window, so the bar doesn't tighten to impossibility forever); pass
    an explicit `family_window=(lo, hi)` to override. challenger_samples ↔ variant_a; baseline_samples
    ↔ variant_b. NEVER auto-enables anything."""
    exp = repo.get_experiment(experiment_id)
    if exp is None:
        raise ValueError(f"no experiment {experiment_id}")
    if exp["status"] == "concluded":
        return {"promote": False, "already_concluded": True, "proposal_id": None,
                "reason": "experiment already concluded"}
    # Too few samples to even run the two-sample test -> conclude inconclusive, file nothing.
    if len(challenger_samples) < 2 or len(baseline_samples) < 2:
        repo.conclude_experiment(experiment_id, now_ms, len(challenger_samples),
                                 len(baseline_samples), "inconclusive", None)
        return {"promote": False, "proposal_id": None,
                "reason": "fewer than 2 OOS observations per variant"}
    test = two_sample_test(challenger_samples, baseline_samples)
    lo, hi = family_window if family_window else (max(0, now_ms - family_window_ms), now_ms)
    search_budget = repo.experiment_search_budget(lo, hi)
    verdict = promotion_decision(diff=test["diff"], mde=exp["mde"], p_value=test["p_value"],
                                 n_a=test["n_a"], n_b=test["n_b"], alpha=alpha,
                                 search_budget=search_budget, min_samples=min_samples)
    verdict.update({"test": test, "search_budget": search_budget, "proposal_id": None,
                    "experiment_id": experiment_id})
    # File the proposal FIRST (so a crash before conclude leaves a re-runnable 'running' experiment),
    # then conclude. Re-run finds the now-pending proposal and won't double-file.
    if verdict["promote"]:
        challenger = exp["variant_a"]
        blocked, why = _promotion_blocked(repo, challenger, now_ms, reject_cooldown_ms)
        if blocked:
            verdict["reason"] += f" ({why})"
        else:
            rationale = (f"OOS promotion [exp #{experiment_id}: {exp['hypothesis']}] — "
                         f"{verdict['reason']}. Beats {exp['variant_b']} on {exp['metric']}. "
                         f"[source: promotion-gate]")
            payload = {"strategy": challenger, "action": "enable",
                       "description": f"promote {challenger} (beats {exp['variant_b']} on "
                                      f"{exp['metric']} OOS)"}
            verdict["proposal_id"] = repo.create_proposal(None, "strategy_toggle", payload,
                                                          rationale, now_ms)
    result = "promote" if verdict["promote"] else "inconclusive"
    repo.conclude_experiment(experiment_id, now_ms, test["n_a"], test["n_b"], result,
                             test["p_value"], correction_method="bonferroni")
    return verdict
