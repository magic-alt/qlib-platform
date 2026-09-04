import pandas as pd

from qlib_platform.backtesting.portfolio import PortfolioPolicy, construct_target_portfolio, portfolio_turnover


def test_weights_stay_attached_to_instruments_regression():
    selection = pd.DataFrame(
        {
            "instrument": ["SH600000", "SZ000001", "SH600519"],
            "score": [0.9, 0.8, 0.7],
            "volatility": [0.02, 0.03, 0.025],
        }
    )
    policy = PortfolioPolicy(
        top_n=3, max_position=0.4, max_exposure=0.9, max_group_exposure=0.9, max_turnover=None
    )
    result = construct_target_portfolio(selection, policy)
    assert set(result["instrument"]) == set(selection["instrument"])
    assert (result["target_weight"] > 0).all()
    assert abs(result["target_weight"].sum() - 0.9) < 1e-9


def test_group_and_position_caps():
    selection = pd.DataFrame(
        {
            "instrument": ["SH600000", "SZ000001", "SH600519", "SZ000858"],
            "score": [4, 3, 2, 1],
            "group": ["BANK", "BANK", "CONSUMER", "CONSUMER"],
        }
    )
    policy = PortfolioPolicy(
        top_n=4,
        weighting="rank",
        max_position=0.3,
        max_exposure=0.8,
        max_group_exposure=0.4,
        max_turnover=None,
    )
    result = construct_target_portfolio(selection, policy)
    assert result["target_weight"].max() <= 0.3000001
    assert result.groupby("group")["target_weight"].sum().max() <= 0.4000001
    assert (
        portfolio_turnover(result.set_index("instrument")["target_weight"], pd.Series(dtype=float))
        <= 0.4 + 1e-9
    )
