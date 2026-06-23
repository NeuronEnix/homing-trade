"""Phase 9 #2: the protected-paths guardrail for self-modification.

A code self-mod may only touch ordinary application code; the safety perimeter (risk / kill-switch /
secrets / live-arming / dry-run flag / schema+guard / CI / this guardrail itself) is off-limits.
Tests the per-path classifier, the exact protected files, directory + glob protection, path
normalization, that ordinary code is allowed, and the fail-closed assert."""
import pytest

from homing_trade import self_modify as sm


@pytest.mark.parametrize("path", [
    "homing_trade/live_broker.py",      # live-arming + dry-run
    "homing_trade/risk.py",             # risk limits + kill-switch
    "homing_trade/config.py",           # leverage/risk/live flags
    "homing_trade/db.py",               # proposal guard + schema
    "homing_trade/proposals.py",        # apply gate
    "homing_trade/comms.py",            # secrets/webhook
    "homing_trade/dotenv.py",           # secret/.env reader
    "homing_trade/advisor.py",          # sizing policy
    "homing_trade/broker.py",           # size/stop/liquidation math
    "homing_trade/position_manager.py", # the kill-switch caller
    "homing_trade/engine.py",           # execution orchestration
    "homing_trade/self_modify.py",      # the guardrail must protect itself
])
def test_perimeter_files_are_protected(path):
    assert sm.is_protected(path)


@pytest.mark.parametrize("path", [
    "homing_trade/../homing_trade/risk.py",   # '..' traversal back to a protected file
    "homing_trade//risk.py",                  # double slash
    "foo/../homing_trade/live_broker.py",     # detour through another dir
    "x/../.github/workflows/ci.yml",          # '..' into a protected dir
    "homing_trade/risk.py/",                  # trailing slash
    "homing_trade/RISK.PY",                   # case variation (FS is case-insensitive)
    "homing_trade/Risk.py",
    "HOMING_TRADE/risk.py",
    "/repo/homing_trade/risk.py",             # absolute path
    "../outside/passwd",                      # escaping the repo -> fail closed
])
def test_path_trick_bypasses_are_blocked(path):
    assert sm.is_protected(path)


@pytest.mark.parametrize("path", [
    ".github/workflows/ci.yml",         # CI gate
    ".github/PULL_REQUEST_TEMPLATE.md",
    "data/paper_trading.db",            # live DB (dir AND glob)
    ".git/config",
    ".env", ".env.local", "prod.env",
    "secrets.key", "server.pem", "creds.p12",
    "anything.sqlite3",
    "homing_trade/my_secret_helper.py", # '*secret*' glob
])
def test_dirs_globs_and_secrets_protected(path):
    assert sm.is_protected(path)


@pytest.mark.parametrize("path", [
    "homing_trade/skills/ma_trend.py",
    "homing_trade/skills/supertrend.py",
    "homing_trade/indicators.py",
    "homing_trade/walkforward.py",
    "homing_trade/web_assets/dashboard.html",
    "tests/test_walkforward.py",
    "README.md",
    "ROADMAP.md",
    "tools/check_roadmap.py",
])
def test_ordinary_code_is_allowed(path):
    assert not sm.is_protected(path)


def test_path_normalization():
    assert sm.is_protected("./homing_trade/risk.py")        # leading ./
    assert sm.is_protected("./.github/workflows/ci.yml")    # leading ./ must NOT eat the dotfile-dir dot
    assert sm.is_protected("  homing_trade/risk.py  ")      # surrounding whitespace
    assert sm.is_protected("homing_trade\\risk.py")         # backslash separator
    assert not sm.is_protected("")                          # empty -> not protected (no crash)


def test_protected_violations_dedups_and_filters():
    paths = ["homing_trade/skills/grid.py", "homing_trade/risk.py", "homing_trade/risk.py",
             ".env", "README.md"]
    assert sm.protected_violations(paths) == ["homing_trade/risk.py", ".env"]


def test_assert_safe_to_modify_passes_on_clean_diff():
    assert sm.assert_safe_to_modify(["homing_trade/skills/macd.py", "tests/test_new_algos.py"]) is True


def test_assert_safe_to_modify_raises_on_protected():
    with pytest.raises(PermissionError) as exc:
        sm.assert_safe_to_modify(["homing_trade/skills/macd.py", "homing_trade/live_broker.py"])
    assert "live_broker.py" in str(exc.value)
    # the message must NOT leak the clean file as a violation
    assert "macd.py" not in str(exc.value)


def test_fail_closed_on_non_string_and_missing_list():
    # non-string change entries can't be verified -> treated as protected (fail closed)
    assert sm.is_protected(None) is True
    assert sm.is_protected(123) is True
    with pytest.raises(PermissionError):
        sm.assert_safe_to_modify(["homing_trade/skills/macd.py", None])
    # a missing path list fails closed rather than passing as "clean"
    with pytest.raises(PermissionError):
        sm.assert_safe_to_modify(None)


def test_bare_protected_dir_name_is_protected():
    assert sm.is_protected(".github") and sm.is_protected("data") and sm.is_protected(".git")
