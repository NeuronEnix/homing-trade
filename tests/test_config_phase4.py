from homing_trade.config import CONFIG


def test_phase4_defaults():
    assert CONFIG.alert_mode == "console"
    assert CONFIG.alert_log_path == "data/alerts.log"
    assert CONFIG.webhook_url == ""
    assert CONFIG.live_enabled is False
    assert CONFIG.live_dry_run is True
    assert CONFIG.coindcx_key_env == "COINDCX_API_KEY"
    assert CONFIG.coindcx_secret_env == "COINDCX_API_SECRET"
    assert CONFIG.daemon_status_path == "data/daemon_status.json"
    assert CONFIG.daemon_backoff_seconds == 5
