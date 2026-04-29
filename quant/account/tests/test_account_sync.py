import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.account.account import CryptoAccount
from quant.account.sync import AccountSyncAdapter, AccountSynchronizer
from quant.schemas import (
    AccountBalanceSnapshot,
    AccountPositionSnapshot,
    AccountSyncSnapshot,
    PayloadSource,
    PositionSide,
    TradeSide,
)


class StaticAccountSyncAdapter(AccountSyncAdapter):
    def __init__(self, snapshot):
        self.snapshot = snapshot

    @property
    def name(self):
        return "static"

    def get_account_snapshot(self):
        return self.snapshot


def make_snapshot():
    return AccountSyncSnapshot(
        account_id="acct-001",
        source=PayloadSource.LIVE,
        observed_at=1710000000,
        venue="okx",
        base_asset="usdt",
        equity=10125.0,
        balances=[
            AccountBalanceSnapshot(asset="usdt", total=10000.0, available=9500.0, locked=500.0),
            AccountBalanceSnapshot(asset="btc", total=0.1, available=0.1),
        ],
        positions=[
            AccountPositionSnapshot(
                symbol="BTC-USDT",
                side=PositionSide.LONG,
                quantity=0.1,
                avg_price=50000.0,
                market_price=51250.0,
                strategy_id="manual-holding",
                correlation_group="crypto-major",
            ),
            AccountPositionSnapshot(
                symbol="ETH-USDT",
                side=PositionSide.SHORT,
                quantity=1.0,
                avg_price=3000.0,
                market_price=2950.0,
            ),
        ],
    )


def test_account_sync_snapshot_round_trip_and_portfolio_projection():
    snapshot = make_snapshot()

    restored = AccountSyncSnapshot.from_payload(snapshot.to_payload())
    portfolio_positions = restored.to_portfolio_positions(default_strategy_id="broker-sync")

    assert restored.base_asset == "USDT"
    assert restored.holding_symbols == ["BTC-USDT", "ETH-USDT"]
    assert restored.balance_for("usdt").available == 9500.0
    assert portfolio_positions[0].symbol == "BTC-USDT"
    assert portfolio_positions[0].side == TradeSide.BUY
    assert portfolio_positions[0].strategy_id == "manual-holding"
    assert portfolio_positions[1].side == TradeSide.SELL
    assert portfolio_positions[1].strategy_id == "broker-sync"


def test_account_synchronizer_applies_broker_truth_to_crypto_account():
    snapshot = make_snapshot()
    account = CryptoAccount(initial_balance=5000.0)
    synchronizer = AccountSynchronizer(StaticAccountSyncAdapter(snapshot))

    returned = synchronizer.sync_crypto_account(account)

    assert returned == snapshot
    assert account.balance == 9500.0
    assert account.equity == 10125.0
    assert account.positions["BTC-USDT"].size == 0.1
    assert account.positions["BTC-USDT"].side == "long"
    assert account.positions["ETH-USDT"].size == -1.0
    assert account.positions["ETH-USDT"].side == "short"
    assert account.market_prices["BTC-USDT"] == 51250.0
    assert account.unrealized_pnl == pytest.approx(175.0)


def test_account_sync_adapter_exposes_holding_symbols():
    adapter = StaticAccountSyncAdapter(make_snapshot())

    assert adapter.name == "static"
    assert adapter.list_holding_symbols() == ["BTC-USDT", "ETH-USDT"]


def test_account_sync_snapshot_rejects_invalid_balances_and_flat_positions():
    with pytest.raises(ValidationError, match="available plus locked"):
        AccountBalanceSnapshot(asset="USDT", total=10.0, available=8.0, locked=3.0)

    with pytest.raises(ValidationError, match="long or short"):
        AccountPositionSnapshot(
            symbol="BTC-USDT",
            side=PositionSide.FLAT,
            quantity=1.0,
            avg_price=50000.0,
        )

    with pytest.raises(ValidationError, match="duplicate assets"):
        AccountSyncSnapshot(
            account_id="acct-001",
            observed_at=1710000000,
            equity=100.0,
            balances=[
                AccountBalanceSnapshot(asset="USDT", total=100.0, available=100.0),
                AccountBalanceSnapshot(asset="usdt", total=100.0, available=100.0),
            ],
        )
