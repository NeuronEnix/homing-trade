# tests/test_no_browser_reload.py
# Dev reloads must not reopen the browser on every restart. devwatch runs its child with
# HT_NO_BROWSER=1; web honors that env (and an explicit --no-browser flag). A direct
# `python -m homing_trade.web` with neither still opens the browser once.
from homing_trade import web, devwatch


def test_should_open_browser_default_true():
    assert web.should_open_browser(["homing_trade.web"], {}) is True


def test_no_browser_flag_disables():
    assert web.should_open_browser(["homing_trade.web", "--no-browser"], {}) is False


def test_ht_no_browser_env_disables():
    assert web.should_open_browser(["homing_trade.web"], {"HT_NO_BROWSER": "1"}) is False


def test_devwatch_child_env_suppresses_browser():
    env = devwatch.child_env({"PATH": "/x", "HOME": "/h"})
    assert env["HT_NO_BROWSER"] == "1"
    assert env["PATH"] == "/x" and env["HOME"] == "/h"   # base env preserved
