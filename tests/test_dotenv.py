import os
from homing_trade.dotenv import load_dotenv, coindcx_keys
from homing_trade.config import Config
from homing_trade.live_broker import LiveBroker


def test_load_dotenv_parses_and_sets_env(tmp_path, monkeypatch):
    monkeypatch.delenv("HT_TEST_X", raising=False)
    p = tmp_path / ".env"
    p.write_text('# comment\nHT_TEST_X="hello"\nHT_TEST_Y=42\n\n', encoding="utf-8")
    loaded = load_dotenv(str(p))
    assert loaded["HT_TEST_X"] == "hello"  # quotes stripped
    assert loaded["HT_TEST_Y"] == "42"
    assert os.environ["HT_TEST_X"] == "hello"


def test_load_dotenv_keeps_existing_unless_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HT_TEST_Z", "already")
    p = tmp_path / ".env"
    p.write_text("HT_TEST_Z=fromfile\n", encoding="utf-8")
    load_dotenv(str(p))
    assert os.environ["HT_TEST_Z"] == "already"   # not overridden
    load_dotenv(str(p), override=True)
    assert os.environ["HT_TEST_Z"] == "fromfile"  # overridden


def test_load_dotenv_missing_file_is_ok():
    assert load_dotenv("/no/such/.env") == {}


def test_coindcx_keys_reads_configured_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COINDCX_API_KEY", "K")
    monkeypatch.setenv("COINDCX_API_SECRET", "S")
    key, secret = coindcx_keys(Config(), dotenv_path=str(tmp_path / "missing.env"))
    assert key == "K" and secret == "S"


def test_live_broker_from_env_is_dry_run_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("COINDCX_API_KEY", "K")
    monkeypatch.setenv("COINDCX_API_SECRET", "S")
    lb = LiveBroker.from_env(Config(), dotenv_path=str(tmp_path / "missing.env"))
    assert lb.dry_run is True  # safe default — must opt into live explicitly
    assert lb.api_key == "K" and lb.api_secret == "S"
    # dry-run path makes no network call:
    assert lb.place_order("BTCINR", "buy", "market_order", 0.001, 0.0, 1)["status"] == "dry_run"
