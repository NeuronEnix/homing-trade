from algotrading.skills.indicators import ema, rsi


def test_ema_insufficient_data_returns_none():
    assert ema([1, 2], 5) is None


def test_ema_constant_series_equals_value():
    assert ema([10.0] * 20, 5) == 10.0


def test_ema_recent_weighting():
    # rising series -> EMA below the last value but above the mean
    series = [float(i) for i in range(1, 21)]  # 1..20
    val = ema(series, 5)
    assert 17.0 < val < 20.0


def test_rsi_insufficient_data_returns_none():
    assert rsi([1, 2, 3], 14) is None


def test_rsi_all_gains_is_100():
    series = [float(i) for i in range(1, 30)]  # strictly increasing
    assert rsi(series, 14) == 100.0


def test_rsi_all_losses_is_low():
    series = [float(i) for i in range(30, 1, -1)]  # strictly decreasing
    assert rsi(series, 14) == 0.0


def test_rsi_flat_series_returns_50():
    # No gains and no losses -> neutral RSI by contract
    assert rsi([100.0] * 30, 14) == 50.0
