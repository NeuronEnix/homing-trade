from algotrading.config import CONFIG


def test_phase3_defaults():
    assert CONFIG.agent_mode == "heuristic"
    assert CONFIG.llm_model == "claude-opus-4-8"
    assert CONFIG.rl_alpha == 0.1
    assert CONFIG.rl_gamma == 0.95
    assert CONFIG.rl_epsilon == 0.1
    assert CONFIG.committee_threshold == 0.2
    assert CONFIG.risk_vol_window == 20
    assert CONFIG.risk_vol_threshold == 0.04
    assert CONFIG.allocator_enabled is False
    assert CONFIG.allocator_lookback == 20
    assert CONFIG.qtable_dir == "data"
