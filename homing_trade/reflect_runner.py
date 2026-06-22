"""Cadence wiring for the PERIODIC reflection loop — makes ReflectionEngine.run_once actually
fire in production (it was a dormant capability until now).

`ReflectionRunner` runs the batched retrospection on a slow wall-clock cadence (default hourly).
It is decoupled from the candle loop in CADENCE (it consults at most every poll_sec, independent
of the candle interval) but NOT in thread: like the AI poll, it runs synchronously inside the
engine's run_tick, so an enabled reflection blocks the loop for the duration of the model call
(bounded by reflection_cli_timeout). For each strategy it calls run_once at most every poll_sec
seconds; run_once's own embargo + watermark prevent re-reflecting the same trades. It is gated:
with no reflect_fn (reflection disabled) it is a no-op, and it never lets a model/DB failure crash
the engine loop.

`build_reflect_fn(cfg)` is the production factory: a `callable(prompt) -> raw_text` using the
same Claude backend as the AI traders (CLI = uses Claude Code, no API key). It returns None
unless `reflection_enabled`, so reflection stays OFF — and unbilled — until explicitly turned on.
Nothing it produces is auto-applied: run_once only FILES human-gated proposals.
"""
import json
import time

from homing_trade.reflection import ReflectionEngine


class ReflectionRunner:
    def __init__(self, repo, reflect_fn=None, *, poll_sec=3600, min_trades=5,
                 starting_balance=5000.0, model="reflection", clock=None):
        self.repo = repo
        self.reflect_fn = reflect_fn
        self.enabled = reflect_fn is not None
        self.engine = ReflectionEngine(repo, reflect_fn, starting_balance=starting_balance,
                                       min_trades=min_trades, model=model)
        self.poll_sec = poll_sec
        self._clock = clock or time.time
        self._last = {}                       # strategy -> last wall-clock reflection time (s)

    def run(self, strategies):
        """Reflect over each strategy whose cadence is due. Returns the run_once summaries that
        produced a reflection (possibly empty). A no-op when disabled; never raises."""
        if not self.enabled:
            return []
        now = self._clock()
        out = []
        for s in strategies:
            last = self._last.get(s)
            if last is not None and (now - last) < self.poll_sec:
                continue                      # cadence not yet due for this strategy
            self._last[s] = now               # stamp before the call so a slow/failed call
            try:                              # still spaces out the next attempt by poll_sec
                res = self.engine.run_once(s, int(now * 1000))
            except Exception:
                res = None                    # belt-and-suspenders; run_once already never raises
            if res:
                out.append(res)
        return out


def build_reflect_fn(cfg):
    """A `callable(prompt) -> raw_text` for ReflectionEngine, or None when reflection is disabled
    (so the runner no-ops and nothing is billed). Mirrors the AI-trader backend; never imports
    its SDK/subprocess until actually called."""
    if not getattr(cfg, "reflection_enabled", False):
        return None
    backend = getattr(cfg, "reflection_backend", "cli")
    model = getattr(cfg, "reflection_model", "") or cfg.llm_model
    timeout = getattr(cfg, "reflection_cli_timeout", 180)
    max_tokens = getattr(cfg, "reflection_max_tokens", 800)

    def _cli(prompt):
        """Shell to the local `claude` CLI (headless) — uses existing Claude Code auth, no key."""
        import subprocess
        cmd = ["claude", "-p", prompt + "\n\nRespond with ONLY the JSON object, no prose.",
               "--output-format", "json"]
        if model:
            cmd += ["--model", model]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"claude cli rc={proc.returncode}: {proc.stderr[:200]}")
        env = json.loads(proc.stdout)
        if env.get("is_error"):
            raise RuntimeError(f"claude cli error: {str(env.get('result', ''))[:200]}")
        return str(env.get("result", ""))

    def _api(prompt):
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(model=model, max_tokens=max_tokens,
                                       messages=[{"role": "user", "content": prompt}])
        return next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")

    return _cli if backend == "cli" else _api
