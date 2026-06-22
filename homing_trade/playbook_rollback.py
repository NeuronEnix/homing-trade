"""Phase-4 #9: per-playbook-version performance slope -> an auto rollback proposal.

When a strategy's CURRENT active playbook performs materially worse than the PARENT version it
superseded (the "slope" is negative), file a human-gated proposal to roll back to the parent's
rules. Because playbooks are append-only, a rollback is published as a NEW version carrying the
parent's rules (superseding the degrading current one); the apply step (ProposalApplier) does the
publish+supersede atomically. PROPOSE-only, idempotent, mechanical (no LLM).
"""
import json

from homing_trade.selfquery import SelfQuery


def propose_rollback(repo, strategy, now_ms, *, min_trades=8, margin=0.0,
                     starting_balance=5000.0, source_reflection_id=None):
    """File a rollback-to-parent proposal if the current playbook degraded vs its parent; return
    its id, else None. `min_trades` is required on BOTH versions (enough evidence); `margin` is how
    much worse the current avg PnL must be than the parent's before acting."""
    current = repo.latest_playbook(strategy)
    if not current or not current.get("parent_version"):
        return None                                          # nothing to roll back to
    # Don't oscillate: a rollback already restored known-good rules, and its parent is the very
    # version we deliberately abandoned. Rolling back AGAIN would walk straight back onto those
    # bad rules. Moving on from a rollback is a job for a forward reflection proposal, not another
    # rollback. (We own the '-rollback-' version naming, so this marker is reliable.)
    if "-rollback-" in (current.get("version") or ""):
        return None
    parent = repo.get_playbook(current["parent_version"])
    if not parent:
        return None
    perf = SelfQuery(repo, starting_balance).playbook_performance(strategy, as_of=now_ms)
    cur_p, par_p = perf.get(current["version"]), perf.get(parent["version"])
    if not cur_p or not par_p:
        return None
    if cur_p["trades"] < min_trades or par_p["trades"] < min_trades:
        return None                                          # not enough evidence on both
    # Degradation requires BOTH a worse average AND a worse win rate — a single fat tail can flip
    # avg_pnl on small samples, so we never act on the mean alone.
    degraded = (cur_p["avg_pnl"] < par_p["avg_pnl"] - margin
                and cur_p["win_rate"] < par_p["win_rate"])
    if not degraded:
        return None
    # Idempotent: don't stack rollback proposals to the same parent.
    for p in repo.pending_proposals(strategy):
        if p["kind"] == "playbook":
            try:
                if json.loads(p["payload_json"]).get("rollback_to") == parent["version"]:
                    return None
            except Exception:
                pass
    try:
        rules = json.loads(parent["rules_json"]).get("rules", [])
    except Exception:
        rules = []
    if not rules:
        return None                                          # nothing to restore
    rationale = (f"Playbook {current['version']} avg PnL {cur_p['avg_pnl']:.2f} over "
                 f"{cur_p['trades']} trades is worse than parent {parent['version']} "
                 f"{par_p['avg_pnl']:.2f} over {par_p['trades']}; roll back to "
                 f"{parent['version']}'s rules.")
    return repo.create_proposal(
        strategy, "playbook",
        {"version": f"{strategy}-rollback-{now_ms}", "rules": rules,
         "parent_version": current["version"], "rollback_to": parent["version"]},
        rationale, now_ms, source_reflection_id=source_reflection_id)
