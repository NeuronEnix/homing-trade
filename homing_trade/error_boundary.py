"""Backlog: per-skill ErrorBoundary — crash isolation + a consecutive-failure circuit breaker.

In the always-on loop every strategy's `on_candle` runs inside one tick loop; an unhandled exception
in ONE skill would otherwise abort the whole tick (every later skill skipped, the daemon possibly
crashing). ErrorBoundary lets the engine isolate each skill: a raise is caught and counted, never
propagated, so the rest of the roster keeps trading. After `threshold` CONSECUTIVE failures a skill
is TRIPPED (auto-disabled) and skipped on future ticks until `reset()` (a manual re-enable or a
process restart) — a broken skill stops wasting cycles and stops emitting noise. A clean run resets
the count, so a transient blip never trips the breaker.

Scope: HARD exceptions only (a skill that raises). Soft AI-consult errors (an llm_trader that returns
a HOLD Signal with `.error` set) are a separate, already-handled, often-transient path and do NOT
count here — folding them in would over-trip the breaker on mere network flakiness.

This class is pure bookkeeping (no I/O); the engine owns the side effects (risk_event + alert on a
trip, equity continuity) so the breaker stays trivially testable.
"""


class ErrorBoundary:
    def __init__(self, threshold=3):
        # bool is an int subclass — exclude it so True can't pose as a threshold of 1.
        if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1:
            raise ValueError(f"threshold must be a positive int, got {threshold!r}")
        self.threshold = threshold
        self._fails = {}       # name -> consecutive failure count (cleared on success / trip / reset)
        self._tripped = {}     # name -> the error string that tripped it

    def is_tripped(self, name):
        return name in self._tripped

    def tripped_reason(self, name):
        return self._tripped.get(name)

    def tripped_skills(self):
        return dict(self._tripped)

    def consecutive_failures(self, name):
        return self._fails.get(name, 0)

    def record_success(self, name):
        """A clean run clears the consecutive-failure count. Does NOT un-trip: a tripped skill is
        skipped (never runs), so it cannot run-to-succeed — recovery is explicit via reset()."""
        self._fails.pop(name, None)

    def record_failure(self, name, error):
        """Count one failure; trip at the threshold. Returns True ONLY the moment it newly trips, so
        the caller alerts/records exactly once. A failure on an already-tripped skill is a no-op."""
        if name in self._tripped:
            return False
        n = self._fails.get(name, 0) + 1
        if n >= self.threshold:
            self._tripped[name] = str(error)
            self._fails.pop(name, None)
            return True
        self._fails[name] = n
        return False

    def reset(self, name):
        """Re-enable a tripped skill and clear its counters. Returns True if it had been tripped."""
        self._fails.pop(name, None)
        return self._tripped.pop(name, None) is not None
