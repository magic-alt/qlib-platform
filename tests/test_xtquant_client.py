from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from tushare_qlib.qmt_gateway.config import GatewaySettings
from tushare_qlib.qmt_gateway.xtquant_client import XtQuantReadOnlyClient, _event_time


def test_xtquant_client_reconnects_with_new_session_after_callback(tmp_path: Path, monkeypatch):
    package = ModuleType("xtquant")
    constants = ModuleType("xtquant.xtconstant")
    constants.STOCK_BUY = 23
    constants.STOCK_SELL = 24
    trader_module = ModuleType("xtquant.xttrader")
    type_module = ModuleType("xtquant.xttype")

    class Callback:
        def on_disconnected(self) -> None: pass

    class Trader:
        instances: list["Trader"] = []

        def __init__(self, path: str, session_id: int) -> None:
            self.path = path
            self.session_id = session_id
            self.callback = None
            self.stopped = False
            self.__class__.instances.append(self)

        def register_callback(self, callback) -> None:
            self.callback = callback

        def start(self) -> None: pass

        def connect(self) -> int: return 0

        def subscribe(self, account) -> int: return 0

        def stop(self) -> None: self.stopped = True

        def query_stock_asset(self, account):
            return SimpleNamespace(total_asset=100.0, cash=50.0)

    class Account:
        def __init__(self, account_id: str, account_type: str) -> None:
            self.account_id = account_id
            self.account_type = account_type

    package.xtconstant = constants
    trader_module.XtQuantTrader = Trader
    trader_module.XtQuantTraderCallback = Callback
    type_module.StockAccount = Account
    monkeypatch.setitem(sys.modules, "xtquant", package)
    monkeypatch.setitem(sys.modules, "xtquant.xtconstant", constants)
    monkeypatch.setitem(sys.modules, "xtquant.xttrader", trader_module)
    monkeypatch.setitem(sys.modules, "xtquant.xttype", type_module)
    userdata = tmp_path / "userdata_mini"
    userdata.mkdir()
    settings = GatewaySettings(userdata, "test-account", "STOCK", 18001, "test-token", tmp_path / "state")
    client = XtQuantReadOnlyClient(settings)

    assert client.query_asset().total_asset == 100.0
    first = Trader.instances[0]
    first.callback.on_disconnected()
    assert client.query_asset().cash == 50.0
    assert len(Trader.instances) == 2
    assert Trader.instances[1].session_id != 18001
    client.close()
    assert Trader.instances[1].stopped


def test_qmt_epoch_seconds_and_milliseconds_convert_to_utc():
    assert _event_time(1786501815) == "2026-08-12T02:30:15Z"
    assert _event_time(1786501815000) == "2026-08-12T02:30:15Z"
