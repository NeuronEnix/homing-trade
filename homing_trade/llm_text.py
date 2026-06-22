"""Shared text-completion adapter for the INTERNAL LLM loops (reflection, research).

Returns a `callable(prompt) -> raw_text` over the Claude CLI (headless, uses existing Claude Code
auth — no API key) or the Anthropic API. Distinct from `llm_backends`, which produces STRUCTURED
trade decisions for the live traders; these internal loops just want prose/JSON text back. The
SDK/subprocess is imported lazily, and callers wrap the invocation so a failure degrades to a
no-op rather than crashing the loop.
"""
import json


def text_completion_fn(backend, model, *, timeout=180, max_tokens=800):
    """A `callable(prompt) -> raw_text`. backend 'cli' shells to `claude` headless (no key);
    anything else uses the Anthropic SDK. Raises on failure (the caller degrades)."""
    def _cli(prompt):
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
