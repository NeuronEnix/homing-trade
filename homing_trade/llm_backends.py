"""LLM backend adapters — a common interface so ANY provider can drive an `LlmTrader` brain.

Each adapter is a callable `(BackendRequest) -> (decision_dict, raw_text)`. It MAY raise (missing
SDK, missing key, unreachable server, network error, malformed JSON); the caller
(`LlmTrader.on_candle`) catches every exception and degrades to HOLD, so a backend can never crash
the trading loop. Provider SDKs are imported LAZILY inside each adapter, so an absent library costs
nothing and simply surfaces as "this backend is unavailable -> HOLD".

This module is a leaf: it imports nothing from the rest of `homing_trade`, so `llm_trader` and
`ai_traders` can both depend on it without a cycle. `_extract_json` lives here (re-exported by
llm_trader for back-compat).

Registered backends:
  cli      — local `claude` headless CLI (uses existing Claude Code auth, no API key)
  api      — Anthropic SDK (ANTHROPIC_API_KEY)
  openai   — OpenAI SDK (OPENAI_API_KEY)
  mistral  — Mistral SDK (MISTRAL_API_KEY)
  llama    — local / OpenAI-compatible endpoint (Ollama, llama.cpp server, vLLM) via the openai SDK
"""
import json
import os
import subprocess
from dataclasses import dataclass


def _extract_json(text):
    """Pull the first {...} JSON object out of an LLM's text reply."""
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1 or e < s:
        raise ValueError("no JSON object in LLM response")
    return json.loads(text[s:e + 1])


@dataclass(frozen=True)
class BackendRequest:
    """Everything an adapter needs to make one decision. `client` lets a caller inject an SDK
    client (tests, or reuse); when None the adapter constructs its own (lazy import)."""
    prompt: str                 # the user content (charts + "Decide the trade")
    system: str                 # the system prompt
    model: str
    max_tokens: int = 600
    schema: dict | None = None  # JSON schema for structured output (providers that support it)
    client: object = None       # injected SDK client; None -> adapter builds one
    cli_timeout: int = 120


def _from_choices(resp):
    """Pull assistant text from an OpenAI/Mistral-shaped chat-completions response."""
    return resp.choices[0].message.content


def _anthropic(req: BackendRequest):
    client = req.client
    if client is None:
        import anthropic  # lazy — only when actually consulting
        client = anthropic.Anthropic()
    kw = dict(model=req.model, max_tokens=req.max_tokens, system=req.system,
              messages=[{"role": "user", "content": req.prompt}])
    if req.schema:
        kw["output_config"] = {"format": {"type": "json_schema", "schema": req.schema}}
    resp = client.messages.create(**kw)
    text = next(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return _extract_json(text), text


def _cli(req: BackendRequest):
    """Shell out to the local `claude` CLI (headless). Heavier per call but no extra billing."""
    prompt = f"{req.system}\n\n{req.prompt}\n\nRespond with ONLY the JSON object, no prose."
    cmd = ["claude", "-p", prompt, "--output-format", "json"]
    if req.model:
        cmd += ["--model", req.model]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=req.cli_timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"claude cli rc={proc.returncode}: {proc.stderr[:300]}")
    env = json.loads(proc.stdout)
    if env.get("is_error"):
        raise RuntimeError(f"claude cli error: {str(env.get('result', ''))[:300]}")
    return _extract_json(str(env.get("result", ""))), proc.stdout


def _openai(req: BackendRequest):
    client = req.client
    if client is None:
        import openai  # lazy
        client = openai.OpenAI()
    resp = client.chat.completions.create(
        model=req.model, max_tokens=req.max_tokens,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": req.system},
                  {"role": "user", "content": req.prompt}],
    )
    text = _from_choices(resp)
    return _extract_json(text), text


def _mistral(req: BackendRequest):
    client = req.client
    if client is None:
        from mistralai import Mistral  # lazy
        client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
    resp = client.chat.complete(
        model=req.model, max_tokens=req.max_tokens,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": req.system},
                  {"role": "user", "content": req.prompt}],
    )
    text = _from_choices(resp)
    return _extract_json(text), text


def _llama(req: BackendRequest):
    """Local / OpenAI-compatible endpoint (Ollama, llama.cpp server, vLLM). Reuses the openai SDK
    pointed at LLAMA_BASE_URL (default the Ollama port). Degrades to HOLD if the SDK is absent or
    the server is unreachable. No structured-output mode (broadest local-server compatibility)."""
    client = req.client
    if client is None:
        import openai  # lazy
        client = openai.OpenAI(base_url=os.environ.get("LLAMA_BASE_URL", "http://localhost:11434/v1"),
                               api_key=os.environ.get("LLAMA_API_KEY", "not-needed"))
    resp = client.chat.completions.create(
        model=req.model, max_tokens=req.max_tokens,
        messages=[{"role": "system", "content": req.system},
                  {"role": "user", "content": req.prompt}],
    )
    text = _from_choices(resp)
    return _extract_json(text), text


# The registry: backend name -> adapter. ai_traders uses set(BACKENDS) as its supported-backend set;
# llm_trader dispatches through decide().
BACKENDS = {
    "cli": _cli,
    "api": _anthropic,
    "openai": _openai,
    "mistral": _mistral,
    "llama": _llama,
}


def decide(backend: str, req: BackendRequest):
    """Dispatch one decision to the named backend. Raises ValueError for an unknown backend; any
    provider/SDK/network error propagates (the caller degrades to HOLD)."""
    try:
        adapter = BACKENDS[backend]
    except KeyError:
        raise ValueError(f"unknown LLM backend: {backend!r}")
    return adapter(req)
