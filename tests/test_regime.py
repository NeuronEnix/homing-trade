from homing_trade.skills.indicators import adx, ema_slope, realized_vol, classify_regime
from homing_trade.models import Candle


def uptrend(n=80, start=100.0, step=0.5):
    cs, p = [], start
    for i in range(n):
        p += step
        cs.append(Candle(open=p - step, high=p + 0.2, low=p - step - 0.1, close=p,
                         volume=1, time=1000 + i * 60000))
    return cs


def downtrend(n=80, start=140.0, step=0.5):
    cs, p = [], start
    for i in range(n):
        p -= step
        cs.append(Candle(open=p + step, high=p + step + 0.1, low=p - 0.2, close=p,
                         volume=1, time=1000 + i * 60000))
    return cs


def flat(n=80, price=100.0):
    return [Candle(open=price, high=price + 0.1, low=price - 0.1, close=price,
                   volume=1, time=1000 + i * 60000) for i in range(n)]


def test_adx_none_when_short():
    assert adx(uptrend(n=5)) is None


def test_adx_in_range_and_high_on_strong_trend():
    a = adx(uptrend())
    assert a is not None and 0.0 <= a <= 100.0
    assert a > 25.0                                   # a clean one-way move is strongly trending


def test_ema_slope_sign_follows_direction():
    closes = [c.close for c in uptrend()]
    assert ema_slope(closes) > 0
    assert ema_slope(list(reversed(closes))) < 0      # reversed series trends down


def test_realized_vol_nonnegative():
    v = realized_vol([c.close for c in uptrend()])
    assert v is not None and v >= 0.0


def test_classify_trend_up():
    r = classify_regime(uptrend())
    assert r["regime"] == "trend_up"
    assert r["adx"] > 25.0 and r["ema_slope"] > 0


def test_classify_chop_on_flat():
    r = classify_regime(flat())
    assert r["regime"] == "chop"                      # flat -> ADX ~0 -> chop
    assert r["adx"] is not None and r["adx"] <= 20.0


def test_classify_trend_down():
    r = classify_regime(downtrend())
    assert r["regime"] == "trend_down" and r["adx"] > 25.0 and r["ema_slope"] < 0


def test_classify_unknown_when_short():
    r = classify_regime(uptrend(n=10))                # too few bars for ADX
    assert r["regime"] == "unknown" and r["adx"] is None


def test_adx_regression_anchor():
    # A fixed zig-zag series pins a known ADX value, guarding the Wilder math against drift.
    seq, base = [], 100.0
    for i in range(40):
        base += (1.5 if (i // 3) % 2 == 0 else -1.0)
        seq.append(Candle(open=base, high=base + 0.8, low=base - 0.8, close=base,
                          volume=1, time=1000 + i * 60000))
    assert round(adx(seq), 4) == 21.9126
