from decimal import Decimal
from typing import Any, Dict, List, Optional

from quant.account.sync import AccountSyncAdapter
from quant.schemas.account import (
    AccountBalanceSnapshot,
    AccountPositionSnapshot,
    AccountSyncSnapshot,
)
from quant.schemas.enums import PayloadSource, PositionSide


class ExchangeAccountParseError(ValueError):
    pass


class OKXAccountSyncAdapter(AccountSyncAdapter):
    """Read-only OKX account parser that emits AccountSyncSnapshot."""

    def __init__(
        self,
        adapter: object,
        *,
        account_id: str,
        base_asset: str = "USDT",
        include_positions: bool = True,
        observed_at: Optional[int] = None,
    ):
        self.adapter = adapter
        self.account_id = account_id
        self.base_asset = base_asset
        self.include_positions = include_positions
        self.observed_at = observed_at

    @property
    def name(self) -> str:
        return "okx"

    def get_account_snapshot(self) -> AccountSyncSnapshot:
        balance_response = self.adapter.get_balance()
        position_response = (
            self.adapter.get_positions()
            if self.include_positions and hasattr(self.adapter, "get_positions")
            else {"data": []}
        )
        balance_item = _first_data_item(balance_response, "OKX balance")
        balances = _parse_okx_balances(balance_item)
        positions = _parse_okx_positions(position_response)
        observed_at = self.observed_at or _latest_timestamp(
            [balance_item],
            _data_items(position_response, "OKX positions"),
            keys=("uTime", "cTime", "ts"),
        )
        equity = _float_first(balance_item, ("totalEq", "eq"))
        if equity is None:
            equity = _equity_from_balances_and_positions(
                balances,
                positions,
                base_asset=self.base_asset,
            )

        return AccountSyncSnapshot(
            account_id=self.account_id,
            source=PayloadSource.LIVE,
            observed_at=observed_at,
            venue="okx",
            base_asset=self.base_asset,
            equity=equity,
            balances=balances,
            positions=positions,
            metadata={
                "parser": "okx_account_sync_v1",
                "read_only": True,
                "balance_path": _response_field(balance_response, "path"),
                "positions_path": _response_field(position_response, "path"),
                "raw_position_count": len(_data_items(position_response, "OKX positions")),
            },
        )


class BinanceAccountSyncAdapter(AccountSyncAdapter):
    """Read-only Binance account parser that emits AccountSyncSnapshot."""

    def __init__(
        self,
        adapter: object,
        *,
        account_id: str,
        base_asset: str = "USDT",
        market_prices: Optional[Dict[str, float]] = None,
        observed_at: Optional[int] = None,
    ):
        self.adapter = adapter
        self.account_id = account_id
        self.base_asset = base_asset
        self.market_prices = {
            _normalize_binance_symbol(symbol): price
            for symbol, price in (market_prices or {}).items()
        }
        self.observed_at = observed_at

    @property
    def name(self) -> str:
        return "binance"

    def get_account_snapshot(self) -> AccountSyncSnapshot:
        account_response = self.adapter.get_account()
        account_item = _first_data_item(account_response, "Binance account")
        balances = _parse_binance_balances(account_item)
        positions = _parse_binance_positions(account_item)
        positions.extend(
            _spot_positions_from_balances(
                balances,
                base_asset=self.base_asset,
                market_prices=self.market_prices,
                existing_symbols={position.symbol for position in positions},
            )
        )
        observed_at = self.observed_at or _latest_timestamp(
            [account_item],
            account_item.get("positions") or [],
            keys=("updateTime", "time"),
        )
        equity = _float_first(
            account_item,
            ("totalEquity", "totalMarginBalance", "totalWalletBalance"),
        )
        if equity is None:
            equity = _equity_from_balances_and_positions(
                balances,
                positions,
                base_asset=self.base_asset,
            )

        return AccountSyncSnapshot(
            account_id=self.account_id,
            source=PayloadSource.LIVE,
            observed_at=observed_at,
            venue="binance",
            base_asset=self.base_asset,
            equity=equity,
            balances=balances,
            positions=positions,
            metadata={
                "parser": "binance_account_sync_v1",
                "read_only": True,
                "account_path": _response_field(account_response, "path"),
                "raw_position_count": len(account_item.get("positions") or []),
                "spot_positions_from_balances": bool(self.market_prices),
            },
        )


def _parse_okx_balances(account_item: Dict[str, Any]) -> List[AccountBalanceSnapshot]:
    balances: List[AccountBalanceSnapshot] = []
    for item in account_item.get("details") or []:
        asset = str(item.get("ccy") or "").strip().upper()
        if not asset:
            continue
        total = _float_first(item, ("eq", "cashBal", "bal", "availBal", "availEq"))
        available = _float_first(item, ("availBal", "availEq", "cashBal"))
        if total is None and available is not None:
            total = available
        if total is None:
            continue
        if available is None:
            available = total
        available = min(max(available, 0.0), total)
        locked = max(total - available, 0.0)
        if total <= 0.0 and available <= 0.0 and locked <= 0.0:
            continue
        balances.append(
            AccountBalanceSnapshot(
                asset=asset,
                total=total,
                available=available,
                locked=locked,
            )
        )
    return balances


def _parse_okx_positions(response: Any) -> List[AccountPositionSnapshot]:
    positions: List[AccountPositionSnapshot] = []
    for item in _data_items(response, "OKX positions"):
        quantity = _float_first(item, ("pos", "positionAmt", "qty"))
        if quantity is None or abs(quantity) <= 0.0:
            continue
        symbol = str(item.get("instId") or item.get("symbol") or "").strip().upper()
        if not symbol:
            raise ExchangeAccountParseError("OKX position missing instId")
        side = _position_side(item.get("posSide"), quantity)
        avg_price = _float_first(item, ("avgPx", "avgPrice", "entryPrice"))
        market_price = _float_first(item, ("markPx", "last", "marketPrice"))
        avg_price = avg_price or market_price
        if avg_price is None or avg_price <= 0.0:
            raise ExchangeAccountParseError(f"OKX position {symbol} missing avg price")
        positions.append(
            AccountPositionSnapshot(
                symbol=symbol,
                side=side,
                quantity=abs(quantity),
                avg_price=avg_price,
                market_price=market_price,
                unrealized_pnl=_float_first(item, ("upl", "unrealizedPnl", "unrealizedProfit")),
            )
        )
    return positions


def _parse_binance_balances(account_item: Dict[str, Any]) -> List[AccountBalanceSnapshot]:
    balances: List[AccountBalanceSnapshot] = []
    for item in account_item.get("balances") or account_item.get("assets") or []:
        asset = str(item.get("asset") or "").strip().upper()
        if not asset:
            continue
        free = _float_first(item, ("free", "availableBalance", "walletBalance")) or 0.0
        locked = _float_first(item, ("locked", "crossUnPnl")) or 0.0
        total = _float_first(item, ("total", "walletBalance"))
        if total is None:
            total = free + max(locked, 0.0)
        available = min(max(free, 0.0), total)
        locked_value = max(total - available, 0.0)
        if total <= 0.0 and available <= 0.0 and locked_value <= 0.0:
            continue
        balances.append(
            AccountBalanceSnapshot(
                asset=asset,
                total=total,
                available=available,
                locked=locked_value,
            )
        )
    return balances


def _parse_binance_positions(account_item: Dict[str, Any]) -> List[AccountPositionSnapshot]:
    positions: List[AccountPositionSnapshot] = []
    for item in account_item.get("positions") or []:
        quantity = _float_first(item, ("positionAmt", "positionAmount", "qty"))
        if quantity is None or abs(quantity) <= 0.0:
            continue
        symbol = _normalize_binance_symbol(item.get("symbol") or item.get("pair") or "")
        if not symbol:
            raise ExchangeAccountParseError("Binance position missing symbol")
        avg_price = _float_first(item, ("entryPrice", "avgPrice"))
        market_price = _float_first(item, ("markPrice", "marketPrice"))
        avg_price = avg_price or market_price
        if avg_price is None or avg_price <= 0.0:
            raise ExchangeAccountParseError(f"Binance position {symbol} missing avg price")
        positions.append(
            AccountPositionSnapshot(
                symbol=symbol,
                side=PositionSide.SHORT if quantity < 0.0 else PositionSide.LONG,
                quantity=abs(quantity),
                avg_price=avg_price,
                market_price=market_price,
                unrealized_pnl=_float_first(item, ("unrealizedProfit", "unRealizedProfit")),
            )
        )
    return positions


def _spot_positions_from_balances(
    balances: List[AccountBalanceSnapshot],
    *,
    base_asset: str,
    market_prices: Dict[str, float],
    existing_symbols: Optional[set[str]] = None,
) -> List[AccountPositionSnapshot]:
    positions: List[AccountPositionSnapshot] = []
    base = base_asset.strip().upper()
    seen = set(existing_symbols or set())
    for balance in balances:
        if balance.asset == base or balance.total <= 0.0:
            continue
        symbol = _normalize_binance_symbol(f"{balance.asset}{base}")
        if symbol in seen:
            continue
        price = market_prices.get(symbol)
        if price is None:
            continue
        seen.add(symbol)
        positions.append(
            AccountPositionSnapshot(
                symbol=symbol,
                side=PositionSide.LONG,
                quantity=balance.total,
                avg_price=price,
                market_price=price,
            )
        )
    return positions


def _data_items(response: Any, label: str) -> List[Dict[str, Any]]:
    if response is None:
        return []
    if isinstance(response, list):
        data = response
    elif isinstance(response, dict):
        data = response.get("data")
        if data is None:
            data = [response]
    else:
        raise ExchangeAccountParseError(f"{label} response must be a dict or list")
    if not isinstance(data, list):
        raise ExchangeAccountParseError(f"{label} data must be a list")
    return [item for item in data if isinstance(item, dict)]


def _first_data_item(response: Any, label: str) -> Dict[str, Any]:
    items = _data_items(response, label)
    if not items:
        raise ExchangeAccountParseError(f"{label} returned no data")
    return items[0]


def _float_first(payload: Dict[str, Any], keys: tuple[str, ...]) -> Optional[float]:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return float(Decimal(str(value)))
    return None


def _latest_timestamp(
    *groups: Any,
    keys: tuple[str, ...],
) -> int:
    timestamps = []
    for group in groups:
        if isinstance(group, dict):
            items = [group]
        else:
            items = group or []
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in keys:
                value = item.get(key)
                if value not in (None, ""):
                    timestamp = int(Decimal(str(value)))
                    if timestamp > 10_000_000_000:
                        timestamp = timestamp // 1000
                    timestamps.append(timestamp)
                    break
    return max(timestamps) if timestamps else 0


def _equity_from_balances_and_positions(
    balances: List[AccountBalanceSnapshot],
    positions: List[AccountPositionSnapshot],
    *,
    base_asset: str,
) -> float:
    base = base_asset.strip().upper()
    base_balance = next((balance for balance in balances if balance.asset == base), None)
    equity = base_balance.total if base_balance is not None else 0.0
    equity += sum(position.notional for position in positions)
    return equity


def _position_side(raw_side: Any, quantity: float) -> PositionSide:
    side = str(raw_side or "").strip().lower()
    if side == "short" or quantity < 0.0:
        return PositionSide.SHORT
    return PositionSide.LONG


def _normalize_binance_symbol(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "").replace("_", "")


def _response_field(response: Any, key: str) -> Optional[str]:
    if isinstance(response, dict) and response.get(key) not in (None, ""):
        return str(response[key])
    return None
