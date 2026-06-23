"""Protected-paths guardrail for self-modification (Phase 9 #2).

When the bot proposes a CODE change to itself (Phase 9 #1), it may only ever touch ordinary
application code — strategies, indicators, the dashboard, docs, tests. It can NEVER modify the files
that hold the safety perimeter: risk limits, the kill-switch, secret handling, live-arming, the
`LiveBroker` dry-run flag, the proposal/approval guard, the schema, OR the very mechanisms that gate
it (this denylist, the CI workflow). A proposed diff that touches any protected path is rejected
before it can ever become a PR.

The check is fail-CLOSED and deliberately broad: protect whole files (not lines), so there is no way
to "edit just the safe part" of a sensitive module — config/risk/live changes go through the existing
human-gated `param` proposal path, not a code PR. `data/`, `.env*`, keys and DBs are protected too so
a self-mod can never exfiltrate or commit a secret/DB.

This module is itself protected (`self_modify.py` is in the denylist): a proposal can never weaken
its own guardrail.
"""
import fnmatch
import posixpath

# Exact application-source files that form the safety perimeter — never self-modifiable. Stored
# lowercased; matched case-insensitively (the FS is case-insensitive on macOS/Windows). Includes not
# just the files that DEFINE a limit but the ones that ENFORCE it, so a self-mod can't neuter the
# kill-switch / sizing / execution by editing a caller instead of risk.py.
_PROTECTED_FILES = frozenset({
    "homing_trade/live_broker.py",     # live-arming + the dry-run flag
    "homing_trade/risk.py",            # risk limits + the daily-loss kill-switch
    "homing_trade/config.py",          # leverage/risk/live flags + secret-env names live here
    "homing_trade/db.py",              # PROTECTED_PROPOSAL_FIELDS guard + schema/migrations
    "homing_trade/proposals.py",       # the apply/approval gate
    "homing_trade/comms.py",           # secret/webhook handling
    "homing_trade/dotenv.py",          # the .env / secret reader (could log or exfiltrate keys)
    "homing_trade/advisor.py",         # position-sizing policy (risk_pct/stop_pct/leverage)
    "homing_trade/broker.py",          # size / stop / liquidation math + hit_stop/hit_liquidation
    "homing_trade/position_manager.py",  # the only code that calls the risk guard / kill-switch
    "homing_trade/engine.py",          # execution orchestration (the tick that calls the guard)
    "homing_trade/self_modify.py",     # THIS guardrail — a proposal must never weaken its own denylist
})

# A path equal to OR under any of these directories is protected.
_PROTECTED_DIRS = (
    ".github",                         # CI + PR template — must not weaken the gate that blocks bad self-mods
    "data",                            # the live DB / runtime data
    ".git",
)

# Glob patterns (matched against the full path AND the basename, lowercased) — secrets, keys, env, DBs.
_PROTECTED_GLOBS = (
    ".env", ".env.*", "*.env",
    "*.key", "*.pem", "*.p12", "*.pfx", "id_rsa*",
    "*.db", "*.sqlite", "*.sqlite3",
    "*secret*", "*credential*", "*token*", "creds*",
)


def _normalize(path):
    """Canonicalize a changed path to a repo-relative posix path, or None if it is not a string.
    Uses posixpath.normpath so `..`, `//` and trailing slashes can't smuggle a protected file past
    the matcher (e.g. 'a/../homing_trade/risk.py' -> 'homing_trade/risk.py')."""
    if not isinstance(path, str):
        return None                    # non-string change entry -> caller fails closed
    p = path.strip().replace("\\", "/")
    if not p:
        return ""
    return posixpath.normpath(p)       # collapses ./  ../  //  and trailing '/'


def is_protected(path):
    """True if a single changed path is in a protected zone. Fail-CLOSED: a non-string entry, an
    absolute path, or a repo-escaping '..' path is treated as protected."""
    p = _normalize(path)
    if p is None:
        return True                    # not a string -> can't verify -> refuse
    if p in ("", ".", ".."):
        return False                   # nothing / the repo root itself -> no file touched
    if posixpath.isabs(p) or p.startswith("../"):
        return True                    # absolute or escaping the repo -> suspicious -> refuse
    low = p.lower()
    if low in _PROTECTED_FILES:
        return True
    if any(low == d or low.startswith(d + "/") for d in _PROTECTED_DIRS):
        return True
    base = low.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(low, g) or fnmatch.fnmatch(base, g) for g in _PROTECTED_GLOBS)


def protected_violations(paths):
    """Return the subset of `paths` that touch a protected zone (order-preserving, deduped). A None
    `paths` is a programming error here — assert_safe_to_modify guards it and fails closed."""
    seen, out = set(), []
    for p in paths:
        key = p if isinstance(p, str) else repr(p)
        if key not in seen and is_protected(p):
            seen.add(key)
            out.append(key)
    return out


def assert_safe_to_modify(paths):
    """Raise PermissionError listing every protected path a proposed diff touches. A no-op when the
    diff is clean. The single chokepoint the self-modification PR proposer must call before opening
    a branch/PR — and that ProposalApplier-style gates can re-assert. Fails CLOSED on a missing list."""
    if paths is None:
        raise PermissionError("self-modification refused — no changed-path list provided "
                              "(cannot verify safety).")
    bad = protected_violations(paths)
    if bad:
        raise PermissionError(
            "self-modification refused — proposed change touches protected path(s): "
            + ", ".join(bad)
            + ". Risk limits, kill-switch, secrets, live-arming, the schema/guard, and CI are "
              "off-limits to code self-mods (config changes go through the human-gated param proposal "
              "path).")
    return True
