import hashlib
import hmac
import json
import pytest
from algotrading.live_broker import sign, build_order_payload, LiveBroker
from algotrading.models import Signal


def test_sign_matches_hmac_sha256_of_compact_json():
    body = {"a": 1, "b": 2}
    expected = hmac.new(b"secret",
                        json.dumps(body, separators=(",", ":")).encode("utf-8"),
                        hashlib.sha256).hexdigest()
    assert sign("secret", body) == expected


def test_build_order_payload_shape():
    p = build_order_payload("B-BTC_USDT", "buy", "market_order", 0.001, 0.0, 1717000000000)
    assert p["market"] == "B-BTC_USDT" and p["side"] == "buy"
    assert p["order_type"] == "market_order" and p["total_quantity"] == 0.001
    assert p["timestamp"] == 1717000000000


def test_dry_run_makes_no_network_call():
    def boom(url, headers, body):
        raise AssertionError("dry-run must NOT call the network")
    lb = LiveBroker(dry_run=True, poster=boom)
    res = lb.place_order("BTCINR", "buy", "market_order", 0.001, 0.0, 1)
    assert res["status"] == "dry_run"
    assert res["payload"]["market"] == "BTCINR"


def test_live_path_signs_and_posts():
    captured = {}
    def poster(url, headers, body):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = body
        return {"status": "ok", "id": "abc"}
    lb = LiveBroker(api_key="K", api_secret="S", dry_run=False, poster=poster)
    res = lb.place_order("BTCINR", "buy", "market_order", 0.001, 0.0, 1)
    assert res == {"status": "ok", "id": "abc"}
    assert captured["headers"]["X-AUTH-APIKEY"] == "K"
    assert captured["headers"]["X-AUTH-SIGNATURE"] == sign("S", captured["body"])
    assert captured["url"].endswith("/exchange/v1/orders/create")


def test_live_path_requires_keys():
    with pytest.raises(ValueError):
        LiveBroker(dry_run=False, poster=lambda u, h, b: {}).place_order("BTCINR", "buy", "market_order", 0.001, 0.0, 1)
    with pytest.raises(ValueError):  # empty-string keys also refused
        LiveBroker(api_key="", api_secret="", dry_run=False, poster=lambda u, h, b: {}).place_order("BTCINR", "buy", "market_order", 0.001, 0.0, 1)


def test_from_signal_maps_actions():
    lb = LiveBroker(dry_run=True)
    assert lb.from_signal(Signal("LONG"), "BTCINR", 0.001, 0.0, 1)["payload"]["side"] == "buy"
    assert lb.from_signal(Signal("CLOSE"), "BTCINR", 0.001, 0.0, 1)["payload"]["side"] == "sell"
    assert lb.from_signal(Signal("SHORT"), "BTCINR", 0.001, 0.0, 1)["payload"]["side"] == "sell"
    assert lb.from_signal(Signal("HOLD"), "BTCINR", 0.001, 0.0, 1)["status"] == "noop"
