from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from quant.schemas.base import SmartQTFModel, TraceContext
from quant.schemas.enums import PayloadSource, PositionSide, TradeSide
from quant.schemas.portfolio import PortfolioPositionSnapshot

if hasattr(BaseModel, "model_validate"):
    from pydantic import field_validator, model_validator
else:
    from pydantic import root_validator, validator


class AccountBalanceSnapshotBase(SmartQTFModel):
    asset: str
    total: float = Field(ge=0.0)
    available: float = Field(ge=0.0)
    locked: float = Field(default=0.0, ge=0.0)

    @classmethod
    def non_empty_asset(cls, value):
        if not value or not str(value).strip():
            raise ValueError("asset must not be empty")
        return str(value).strip().upper()

    @classmethod
    def validate_balance_values(cls, values):
        total = values.get("total")
        available = values.get("available")
        locked = values.get("locked") or 0.0
        if (
            total is not None
            and available is not None
            and available + locked > total + 1e-9
        ):
            raise ValueError("available plus locked cannot exceed total")
        return values


if hasattr(BaseModel, "model_validate"):

    class AccountBalanceSnapshot(AccountBalanceSnapshotBase):
        @field_validator("asset")
        @classmethod
        def validate_asset(cls, value):
            return cls.non_empty_asset(value)

        @model_validator(mode="after")
        def validate_values(self):
            self.validate_balance_values(self.__dict__)
            return self

else:

    class AccountBalanceSnapshot(AccountBalanceSnapshotBase):
        @validator("asset")
        def validate_asset(cls, value):
            return cls.non_empty_asset(value)

        @root_validator
        def validate_values(cls, values):
            return cls.validate_balance_values(values)


class AccountPositionSnapshotBase(SmartQTFModel):
    symbol: str
    side: PositionSide
    quantity: float = Field(gt=0.0)
    avg_price: float = Field(gt=0.0)
    market_price: Optional[float] = Field(default=None, gt=0.0)
    unrealized_pnl: Optional[float] = None
    strategy_id: Optional[str] = None
    correlation_group: Optional[str] = None
    trace: Optional[TraceContext] = None

    @property
    def notional(self) -> float:
        price = self.market_price if self.market_price is not None else self.avg_price
        return self.quantity * price

    @classmethod
    def non_empty_symbol(cls, value):
        if not value or not str(value).strip():
            raise ValueError("symbol must not be empty")
        return str(value).strip()

    @classmethod
    def position_side_must_be_open(cls, value):
        side = value.value if hasattr(value, "value") else value
        if side == PositionSide.FLAT.value:
            raise ValueError("account position snapshots must be long or short")
        return value


if hasattr(BaseModel, "model_validate"):

    class AccountPositionSnapshot(AccountPositionSnapshotBase):
        @field_validator("symbol")
        @classmethod
        def validate_symbol(cls, value):
            return cls.non_empty_symbol(value)

        @field_validator("side")
        @classmethod
        def validate_side(cls, value):
            return cls.position_side_must_be_open(value)

else:

    class AccountPositionSnapshot(AccountPositionSnapshotBase):
        @validator("symbol")
        def validate_symbol(cls, value):
            return cls.non_empty_symbol(value)

        @validator("side")
        def validate_side(cls, value):
            return cls.position_side_must_be_open(value)


class AccountSyncSnapshotBase(SmartQTFModel):
    account_id: str
    source: PayloadSource = PayloadSource.LIVE
    observed_at: int = Field(ge=0)
    venue: Optional[str] = None
    base_asset: str = "USDT"
    equity: float = Field(ge=0.0)
    balances: List[AccountBalanceSnapshot] = Field(default_factory=list)
    positions: List[AccountPositionSnapshot] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    trace: Optional[TraceContext] = None

    @property
    def holding_symbols(self) -> List[str]:
        symbols = []
        seen = set()
        for position in self.positions:
            if position.quantity <= 0.0 or position.symbol in seen:
                continue
            symbols.append(position.symbol)
            seen.add(position.symbol)
        return symbols

    def balance_for(self, asset: str) -> Optional[AccountBalanceSnapshot]:
        normalized = str(asset).strip().upper()
        for balance in self.balances:
            if balance.asset == normalized:
                return balance
        return None

    def base_balance(self) -> Optional[AccountBalanceSnapshot]:
        return self.balance_for(self.base_asset)

    def to_portfolio_positions(
        self,
        *,
        default_strategy_id: str = "broker-sync",
        correlation_groups: Optional[Dict[str, str]] = None,
    ) -> List[PortfolioPositionSnapshot]:
        groups = correlation_groups or {}
        snapshots = []
        for position in self.positions:
            side = self._portfolio_side(position.side)
            snapshots.append(
                PortfolioPositionSnapshot(
                    symbol=position.symbol,
                    strategy_id=position.strategy_id or default_strategy_id,
                    side=side,
                    quantity=position.quantity,
                    avg_price=position.avg_price,
                    market_price=position.market_price,
                    correlation_group=position.correlation_group or groups.get(position.symbol),
                )
            )
        return snapshots

    @classmethod
    def non_empty_string(cls, value, field_name):
        if not value or not str(value).strip():
            raise ValueError(f"{field_name} must not be empty")
        return str(value).strip()

    @classmethod
    def normalize_base_asset(cls, value):
        return cls.non_empty_string(value, "base_asset").upper()

    @classmethod
    def validate_snapshot_values(cls, values):
        balances = values.get("balances") or []
        positions = values.get("positions") or []

        balance_assets = set()
        for balance in balances:
            asset = balance.asset
            if asset in balance_assets:
                raise ValueError("account balances must not contain duplicate assets")
            balance_assets.add(asset)

        position_keys = set()
        for position in positions:
            side = (
                position.side.value
                if hasattr(position.side, "value")
                else position.side
            )
            key = (position.symbol, side)
            if key in position_keys:
                raise ValueError(
                    "account positions must not contain duplicate symbol/side entries"
                )
            position_keys.add(key)
        return values

    @staticmethod
    def _portfolio_side(side):
        side_value = side.value if hasattr(side, "value") else side
        if side_value == PositionSide.LONG.value:
            return TradeSide.BUY
        return TradeSide.SELL


if hasattr(BaseModel, "model_validate"):

    class AccountSyncSnapshot(AccountSyncSnapshotBase):
        @field_validator("account_id")
        @classmethod
        def validate_account_id(cls, value):
            return cls.non_empty_string(value, "account_id")

        @field_validator("base_asset")
        @classmethod
        def validate_base_asset(cls, value):
            return cls.normalize_base_asset(value)

        @model_validator(mode="after")
        def validate_values(self):
            self.validate_snapshot_values(self.__dict__)
            return self

else:

    class AccountSyncSnapshot(AccountSyncSnapshotBase):
        @validator("account_id")
        def validate_account_id(cls, value):
            return cls.non_empty_string(value, "account_id")

        @validator("base_asset")
        def validate_base_asset(cls, value):
            return cls.normalize_base_asset(value)

        @root_validator
        def validate_values(cls, values):
            return cls.validate_snapshot_values(values)
