import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.account.models.crypto import CryptoAccount
from quant.risk.risk_manager import RiskManager


def test_position_sizing():
    account = CryptoAccount(initial_balance=10000.0)
    risk = RiskManager(max_position_pct=0.1, stop_loss_pct=0.02)
    signal = {"signal": "buy", "signal_index": 2}

    order_signal = risk.apply(signal, account, price=100.0)

    assert order_signal["quantity"] == 10.0
    assert order_signal["stop_loss"] == 98.0
    assert order_signal["take_profit"] == 104.0


def test_stop_loss_trigger():
    account = CryptoAccount(initial_balance=10000.0)
    risk = RiskManager(max_position_pct=0.1, stop_loss_pct=0.02)
    signal = {"signal": "buy", "signal_index": 2}
    order_signal = risk.apply(signal, account, price=100.0)

    assert risk.should_stop_loss(order_signal, price=98.0) is True
    assert risk.should_stop_loss(order_signal, price=99.0) is False


def test_max_drawdown_stop():
    account = CryptoAccount(initial_balance=10000.0)
    account.equity = 8900.0
    risk = RiskManager(max_position_pct=0.1, stop_loss_pct=0.02, max_drawdown_pct=0.1)
    signal = {"signal": "buy", "signal_index": 2}

    order_signal = risk.apply(signal, account, price=100.0)

    assert order_signal is None
