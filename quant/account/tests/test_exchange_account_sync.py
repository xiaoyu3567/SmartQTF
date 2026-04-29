import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from adapters.exchange.binance import BinanceAdapter
from quant.account.exchange_sync import (
    BinanceAccountSyncAdapter,
    ExchangeAccountParseError,
    OKXAccountSyncAdapter,
)
from quant.schemas.enums import PositionSide


class FakeOKXAccountRestAdapter:
    def __init__(self):
        self.balance_calls = 0
        self.position_calls = 0

    def get_balance(self):
        self.balance_calls += 1
        return {
            "success": True,
            "exchange": "okx",
            "path": "/api/v5/account/balance",
            "data": [
                {
                    "uTime": "1710000600123",
                    "totalEq": "10125.5",
                    "details": [
                        {
                            "ccy": "USDT",
                            "eq": "10000",
                            "availBal": "9500",
                            "frozenBal": "500",
                            "uTime": "1710000600123",
                        },
                        {
                            "ccy": "BTC",
                            "eq": "0.1",
                            "availBal": "0.1",
                            "uTime": "1710000600123",
                        },
                    ],
                }
            ],
        }

    def get_positions(self):
        self.position_calls += 1
        return {
            "success": True,
            "exchange": "okx",
            "path": "/api/v5/account/positions",
            "data": [
                {
                    "instId": "BTC-USDT-SWAP",
                    "posSide": "long",
                    "pos": "0.2",
                    "avgPx": "50000",
                    "markPx": "51000",
                    "upl": "200",
                    "uTime": "1710000601123",
                },
                {
                    "instId": "ETH-USDT-SWAP",
                    "posSide": "short",
                    "pos": "1",
                    "avgPx": "3000",
                    "markPx": "2900",
                    "upl": "100",
                    "uTime": "1710000600123",
                },
            ],
        }


class FakeBinanceAccountRestAdapter:
    def __init__(self):
        self.account_calls = 0

    def get_account(self):
        self.account_calls += 1
        return {
            "success": True,
            "exchange": "binance",
            "path": "/api/v3/account",
            "data": [
                {
                    "updateTime": 1710000600123,
                    "balances": [
                        {"asset": "USDT", "free": "9000", "locked": "1000"},
                        {"asset": "BTC", "free": "0.2", "locked": "0"},
                        {"asset": "ETH", "free": "0", "locked": "0"},
                    ],
                }
            ],
        }


def test_okx_account_sync_adapter_parses_balances_and_positions_read_only():
    rest = FakeOKXAccountRestAdapter()
    adapter = OKXAccountSyncAdapter(rest, account_id="okx-live-readonly")

    snapshot = adapter.get_account_snapshot()

    assert adapter.name == "okx"
    assert rest.balance_calls == 1
    assert rest.position_calls == 1
    assert snapshot.account_id == "okx-live-readonly"
    assert snapshot.venue == "okx"
    assert snapshot.observed_at == 1710000601
    assert snapshot.equity == 10125.5
    assert snapshot.balance_for("USDT").available == 9500.0
    assert snapshot.balance_for("USDT").locked == 500.0
    assert snapshot.balance_for("BTC").total == 0.1
    assert snapshot.holding_symbols == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    assert snapshot.positions[0].side == PositionSide.LONG.value
    assert snapshot.positions[0].quantity == 0.2
    assert snapshot.positions[0].market_price == 51000.0
    assert snapshot.positions[1].side == PositionSide.SHORT.value
    assert snapshot.positions[1].unrealized_pnl == 100.0
    assert snapshot.metadata["read_only"] is True
    assert snapshot.metadata["balance_path"] == "/api/v5/account/balance"
    assert snapshot.metadata["positions_path"] == "/api/v5/account/positions"


def test_binance_account_sync_adapter_parses_spot_balances_and_optional_holdings():
    rest = FakeBinanceAccountRestAdapter()
    adapter = BinanceAccountSyncAdapter(
        rest,
        account_id="binance-live-readonly",
        market_prices={"BTCUSDT": 50500.0},
    )

    snapshot = adapter.get_account_snapshot()

    assert adapter.name == "binance"
    assert rest.account_calls == 1
    assert snapshot.account_id == "binance-live-readonly"
    assert snapshot.venue == "binance"
    assert snapshot.observed_at == 1710000600
    assert snapshot.balance_for("USDT").total == 10000.0
    assert snapshot.balance_for("BTC").available == 0.2
    assert snapshot.holding_symbols == ["BTCUSDT"]
    assert snapshot.positions[0].symbol == "BTCUSDT"
    assert snapshot.positions[0].avg_price == 50500.0
    assert snapshot.equity == 20100.0
    assert snapshot.metadata["read_only"] is True
    assert snapshot.metadata["account_path"] == "/api/v3/account"
    assert snapshot.metadata["spot_positions_from_balances"] is True


def test_okx_account_sync_adapter_rejects_unparseable_open_position():
    class BadPositionOKXRestAdapter(FakeOKXAccountRestAdapter):
        def get_positions(self):
            return {
                "data": [
                    {
                        "instId": "BTC-USDT-SWAP",
                        "posSide": "long",
                        "pos": "0.2",
                    }
                ]
            }

    adapter = OKXAccountSyncAdapter(BadPositionOKXRestAdapter(), account_id="okx")

    with pytest.raises(ExchangeAccountParseError, match="missing avg price"):
        adapter.get_account_snapshot()


class CapturingBinanceAdapter(BinanceAdapter):
    def __init__(self):
        super().__init__(api_key="key", secret="secret", require_credentials=False)
        self.calls = []

    def _request(self, method, path, *, params=None):
        self.calls.append({"method": method, "path": path, "params": params})
        return {
            "success": True,
            "exchange": "binance",
            "path": path,
            "data": [{"balances": []}],
        }


def test_binance_rest_adapter_exposes_read_only_account_endpoint():
    adapter = CapturingBinanceAdapter()

    response = adapter.get_account()

    assert response["path"] == "/api/v3/account"
    assert adapter.calls == [
        {"method": "GET", "path": "/api/v3/account", "params": None}
    ]
