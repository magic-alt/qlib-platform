import pandas as pd
import pytest

from qlib_platform.backtesting.exposure_overlay import (
    ExposureOverlayPolicy,
    apply_exposure_overlay,
    exposure_scale,
)


def test_exposure_overlay_uses_most_conservative_throttle():
    policy = ExposureOverlayPolicy(enabled=True)

    state = exposure_scale(
        policy,
        realized_annual_volatility=0.30,
        current_drawdown=-0.15,
        signal_dispersion=0.10,
    )

    assert state["volatilityScale"] == pytest.approx(0.5)
    assert state["drawdownScale"] == pytest.approx(0.5)
    assert state["scale"] == pytest.approx(0.5)


def test_hard_drawdown_moves_portfolio_to_cash():
    targets = pd.DataFrame({"instrument": ["A", "B"], "target_weight": [0.4, 0.4]})
    policy = ExposureOverlayPolicy(enabled=True)

    result, state = apply_exposure_overlay(
        targets,
        policy,
        realized_annual_volatility=0.15,
        current_drawdown=-0.20,
        signal_dispersion=0.10,
    )

    assert state["scale"] == 0.0
    assert result["target_weight"].sum() == 0.0
