"""Minimal stdlib `.env` loader — no external dependency.

Reads `KEY=VALUE` lines from a `.env` file into `os.environ` so secrets (like your
CoinDCX API keys) live in a gitignored file instead of in code or shell history.
"""
import os

from homing_trade.config import CONFIG


def load_dotenv(path=".env", override=False):
    """Load `KEY=VALUE` lines from `path` into os.environ.

    Blank lines and `#` comments are ignored. Surrounding quotes are stripped.
    Existing environment variables are kept unless `override=True`. A missing file
    is fine (returns {}). Returns the dict of values parsed from the file.
    """
    if not os.path.exists(path):
        return {}
    loaded = {}
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if " #" in val:                 # strip inline comment (whitespace + #)
                val = val.split(" #", 1)[0]
            val = val.strip().strip('"').strip("'")
            loaded[key] = val
            if override or key not in os.environ:
                os.environ[key] = val
    return loaded


def coindcx_keys(cfg=CONFIG, *, dotenv_path=".env"):
    """Return (api_key, api_secret) for CoinDCX, loading `.env` first if present.

    Reads the env-var names configured on `cfg` (default COINDCX_API_KEY /
    COINDCX_API_SECRET). Returns empty strings if unset — the LiveBroker live path
    raises a clear error in that case, so you can never accidentally trade keyless.
    """
    load_dotenv(dotenv_path)
    return (os.environ.get(cfg.coindcx_key_env, ""),
            os.environ.get(cfg.coindcx_secret_env, ""))
