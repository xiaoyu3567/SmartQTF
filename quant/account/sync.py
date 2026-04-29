from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from quant.account.models.base import Position
from quant.account.models.crypto import CryptoAccount
from quant.schemas.account import AccountSyncSnapshot


class AccountSyncAdapter(ABC):
    """Read-only account snapshot source for broker/account synchronization."""

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_account_snapshot(self) -> AccountSyncSnapshot:
        raise NotImplementedError

    def list_holding_symbols(self) -> List[str]:
        return self.get_account_snapshot().holding_symbols


class AccountSynchronizer:
    """Applies broker account truth to local account and portfolio views."""

    def __init__(
        self,
        adapter: AccountSyncAdapter,
        *,
        cash_asset: str = "USDT",
        default_strategy_id: str = "broker-sync",
        correlation_groups: Optional[Dict[str, str]] = None,
    ):
        self.adapter = adapter
        self.cash_asset = cash_asset
        self.default_strategy_id = default_strategy_id
        self.correlation_groups = correlation_groups or {}

    def fetch_snapshot(self) -> AccountSyncSnapshot:
        return self.adapter.get_account_snapshot()

    def sync_crypto_account(
        self,
        account: CryptoAccount,
        snapshot: Optional[AccountSyncSnapshot] = None,
    ) -> AccountSyncSnapshot:
        snapshot = snapshot or self.fetch_snapshot()
        self.apply_to_crypto_account(account, snapshot, cash_asset=self.cash_asset)
        return snapshot

    def portfolio_positions(self, snapshot: Optional[AccountSyncSnapshot] = None):
        snapshot = snapshot or self.fetch_snapshot()
        return snapshot.to_portfolio_positions(
            default_strategy_id=self.default_strategy_id,
            correlation_groups=self.correlation_groups,
        )

    def holding_symbols(self, snapshot: Optional[AccountSyncSnapshot] = None) -> List[str]:
        snapshot = snapshot or self.fetch_snapshot()
        return snapshot.holding_symbols

    @staticmethod
    def apply_to_crypto_account(
        account: CryptoAccount,
        snapshot: AccountSyncSnapshot,
        *,
        cash_asset: str = "USDT",
    ) -> CryptoAccount:
        cash_balance = snapshot.balance_for(cash_asset)
        if cash_balance is None:
            cash_balance = snapshot.base_balance()
        if cash_balance is not None:
            account.balance = cash_balance.available

        account.realized_pnl = 0.0
        account.positions = {}
        account.market_prices = {}
        for position_snapshot in snapshot.positions:
            account.positions[position_snapshot.symbol] = Position(
                size=AccountSynchronizer._signed_quantity(position_snapshot),
                avg_price=position_snapshot.avg_price,
                side=AccountSynchronizer._position_side(position_snapshot),
            )
            account.market_prices[position_snapshot.symbol] = (
                position_snapshot.market_price or position_snapshot.avg_price
            )

        account.unrealized_pnl = sum(
            position.unrealized_pnl(account.market_prices.get(symbol, position.avg_price))
            for symbol, position in account.positions.items()
        )
        account.equity = snapshot.equity
        return account

    @staticmethod
    def _signed_quantity(position_snapshot) -> float:
        side = (
            position_snapshot.side.value
            if hasattr(position_snapshot.side, "value")
            else position_snapshot.side
        )
        if side == "short":
            return -position_snapshot.quantity
        return position_snapshot.quantity

    @staticmethod
    def _position_side(position_snapshot):
        side = (
            position_snapshot.side.value
            if hasattr(position_snapshot.side, "value")
            else position_snapshot.side
        )
        return "short" if side == "short" else "long"
