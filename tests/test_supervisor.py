import plistlib
import sys

from homing_trade.supervisor import render_launchd_plist, render_systemd_unit, main


def test_launchd_plist_is_valid_and_correct():
    out = render_launchd_plist(python="/venv/bin/python", workdir="/repo", label="com.ht.test")
    d = plistlib.loads(out.encode())                      # parses -> it's well-formed plist
    assert d["Label"] == "com.ht.test"
    assert d["ProgramArguments"] == ["/venv/bin/python", "-m", "homing_trade.daemon"]
    assert d["WorkingDirectory"] == "/repo"
    assert d["RunAtLoad"] is True and d["KeepAlive"] is True   # boot-start + restart-on-exit
    assert d["StandardOutPath"].startswith("/repo") and d["StandardErrorPath"].startswith("/repo")


def test_launchd_defaults_to_current_venv_and_cwd():
    d = plistlib.loads(render_launchd_plist().encode())
    assert d["ProgramArguments"][0] == sys.executable     # current interpreter
    assert d["WorkingDirectory"]                          # cwd filled in


def test_launchd_plist_escapes_xml_special_chars():
    # A repo path can contain & / < / > — the plist must stay well-formed and round-trip the
    # original (unescaped) value through plistlib.
    out = render_launchd_plist(python="/v/py", workdir="/Users/a/R&D/<repo>")
    d = plistlib.loads(out.encode())                      # would raise if malformed
    assert d["WorkingDirectory"] == "/Users/a/R&D/<repo>"


def test_systemd_unit_has_restart_and_boot_install():
    out = render_systemd_unit(python="/venv/bin/python", workdir="/repo")
    assert "ExecStart=/venv/bin/python -m homing_trade.daemon" in out
    assert "WorkingDirectory=/repo" in out
    assert "Restart=always" in out and "RestartSec=5" in out
    assert "WantedBy=multi-user.target" in out            # starts on boot once enabled
    assert "After=network-online.target" in out
    assert "User=" not in out                             # no user line unless requested


def test_systemd_unit_includes_user_when_given():
    out = render_systemd_unit(python="/p", workdir="/w", user="trader")
    assert "User=trader" in out


def test_main_dispatches_by_kind():
    assert "<plist" in main(["--kind", "launchd", "--python", "/p", "--workdir", "/w"])
    assert "[Service]" in main(["--kind", "systemd", "--python", "/p", "--workdir", "/w"])
