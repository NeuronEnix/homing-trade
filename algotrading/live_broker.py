import hashlib
import hmac
import json

ORDER_PATH = "/exchange/v1/orders/create"


def _canonical(body):
    """Canonical JSON serialization for HMAC signature and wire bytes."""
    return json.dumps(body, separators=(",", ":"))


def sign(secret, body):
    return hmac.new(secret.encode("utf-8"), _canonical(body).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def build_order_payload(market, side, order_type, quantity, price, now_ms):
    return {
        "market": market,
        "side": side,                  # "buy" | "sell"
        "order_type": order_type,      # "limit_order" | "market_order"
        "price_per_unit": price,
        "total_quantity": quantity,
        "timestamp": now_ms,
    }


def _requests_poster(url, headers, body):
    import requests
    resp = requests.post(url, headers=headers, data=_canonical(body), timeout=10)
    resp.raise_for_status()
    return resp.json()


class LiveBroker:
    """Guarded CoinDCX order adapter. dry_run=True (default) NEVER makes a network call —
    it returns a simulated ack. Real orders require dry_run=False AND api_key/api_secret
    (read from env by the caller, never hardcoded)."""

    def __init__(self, api_key=None, api_secret=None, dry_run=True,
                 base_url="https://api.coindcx.com", poster=None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.dry_run = dry_run
        self.base_url = base_url
        self._poster = poster or _requests_poster

    def place_order(self, market, side, order_type, quantity, price, now_ms):
        body = build_order_payload(market, side, order_type, quantity, price, now_ms)
        if self.dry_run:
            return {"status": "dry_run", "payload": body}
        if not self.api_key or not self.api_secret:
            raise ValueError("live order requires api_key and api_secret "
                             "(set COINDCX_API_KEY / COINDCX_API_SECRET)")
        headers = {
            "Content-Type": "application/json",
            "X-AUTH-APIKEY": self.api_key,
            "X-AUTH-SIGNATURE": sign(self.api_secret, body),
        }
        return self._poster(f"{self.base_url}{ORDER_PATH}", headers, body)

    def from_signal(self, signal, market, quantity, price, now_ms, order_type="market_order"):
        if signal.action == "LONG":
            return self.place_order(market, "buy", order_type, quantity, price, now_ms)
        if signal.action in ("CLOSE", "SHORT"):
            return self.place_order(market, "sell", order_type, quantity, price, now_ms)
        return {"status": "noop", "action": signal.action}
