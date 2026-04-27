import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.account.models.crypto import CryptoAccount


def test_account_initialization():
    account = CryptoAccount(initial_balance=10000.0)

    assert account.balance == 10000.0
    assert account.equity == 10000.0
    assert account.positions == {}
    assert account.realized_pnl == 0.0
    assert account.unrealized_pnl == 0.0


def test_buy_updates_position_and_balance():
    account = CryptoAccount(initial_balance=10000.0)
    fill = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "fill_price": 100.0,
        "filled_qty": 1.0,
    }

    position = account.on_fill(fill)

    assert account.balance == 9900.0
    assert account.equity == 10000.0
    assert position.size == 1.0
    assert position.avg_price == 100.0
    assert position.side == "long"


def test_sell_realizes_pnl():
    account = CryptoAccount(initial_balance=10000.0)
    account.on_fill({"symbol": "BTCUSDT", "side": "buy", "fill_price": 100.0, "filled_qty": 1.0})

    position = account.on_fill({"symbol": "BTCUSDT", "side": "sell", "fill_price": 120.0, "filled_qty": 1.0})

    assert account.balance == 10020.0
    assert account.equity == 10020.0
    assert account.realized_pnl == 20.0
    assert position.size == 0.0
    assert position.realized_pnl == 20.0


def test_unrealized_pnl():
    account = CryptoAccount(initial_balance=10000.0)
    account.on_fill({"symbol": "BTCUSDT", "side": "buy", "fill_price": 100.0, "filled_qty": 1.0})

    unrealized_pnl = account.update_market_price(110.0, symbol="BTCUSDT")

    assert unrealized_pnl == 10.0
    assert account.unrealized_pnl == 10.0
    assert account.equity == 10010.0


def test_multi_fill_accumulation():
    account = CryptoAccount(initial_balance=10000.0)
    account.on_fill({"symbol": "BTCUSDT", "side": "buy", "fill_price": 100.0, "filled_qty": 1.0})
    position = account.on_fill({"symbol": "BTCUSDT", "side": "buy", "fill_price": 120.0, "filled_qty": 1.0})

    assert account.balance == 9780.0
    assert position.size == 2.0
    assert position.avg_price == 110.0
    assert account.equity == 10020.0
