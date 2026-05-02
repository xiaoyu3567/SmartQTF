from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from quant.schemas.execution import (
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerProtectiveOrderRequest,
    BrokerProtectiveOrderResult,
    BrokerReplaceOrderRequest,
    ExchangeReadinessCheck,
    ExchangeReadinessReport,
    ExchangeReadinessRequest,
)


class BrokerAdapter(ABC):
    """Interface for simulated, paper, and live broker implementations."""

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def place_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, client_order_id: str) -> BrokerOrderResult:
        raise NotImplementedError

    def replace_order(self, request: BrokerReplaceOrderRequest) -> BrokerOrderResult:
        """Cancel/replace an open order without guessing broker state."""
        raise NotImplementedError

    def place_native_protective_order(
        self,
        request: BrokerProtectiveOrderRequest,
    ) -> BrokerProtectiveOrderResult:
        """Place a live-native protective stop/OCO order after the live gate approves."""
        raise NotImplementedError

    def evaluate_exchange_readiness(
        self,
        request: ExchangeReadinessRequest,
    ) -> ExchangeReadinessReport:
        """Return a replayable readiness report before a live order is submitted.

        Implementations may confirm or prepare exchange-specific settings here. The
        default implementation only evaluates provided fixture state and local rule
        loaders, so default tests remain offline and read-only.
        """
        prepared_state = self._prepare_exchange_environment(request)
        exchange_state = {**dict(request.exchange_state or {}), **prepared_state}
        market_snapshot = dict(request.market_snapshot or {})
        checks: List[ExchangeReadinessCheck] = []

        instrument_rules = request.instrument_rules or self._readiness_instrument_rules(request.symbol)
        if request.require_instrument_rules:
            checks.append(
                self._readiness_check(
                    "instrument_rules",
                    instrument_rules is not None,
                    "instrument_rules_available",
                    "instrument_rules_missing",
                    "instrument order rules are available",
                    "instrument order rules are unavailable",
                    metadata={"symbol": request.symbol},
                )
            )

        desired_leverage = request.desired_leverage
        max_leverage = request.max_leverage or self._float_or_none(exchange_state.get("max_leverage"))
        current_leverage = self._float_or_none(exchange_state.get("leverage"))
        if desired_leverage is not None and max_leverage is not None:
            checks.append(
                self._readiness_check(
                    "leverage_limit",
                    desired_leverage <= max_leverage,
                    "leverage_within_limit",
                    "leverage_above_exchange_max",
                    "desired leverage is within exchange maximum",
                    "desired leverage exceeds exchange maximum",
                    metadata={"desired_leverage": desired_leverage, "max_leverage": max_leverage},
                )
            )
        if desired_leverage is not None and current_leverage is not None:
            checks.append(
                self._readiness_check(
                    "leverage_configured",
                    abs(current_leverage - desired_leverage) <= 1e-9,
                    "leverage_configured",
                    "leverage_not_configured",
                    "exchange leverage matches desired leverage",
                    "exchange leverage does not match desired leverage",
                    metadata={"desired_leverage": desired_leverage, "current_leverage": current_leverage},
                )
            )

        self._append_mode_check(
            checks,
            "margin_mode",
            request.margin_mode,
            exchange_state.get("margin_mode"),
        )
        self._append_mode_check(
            checks,
            "position_mode",
            request.position_mode,
            exchange_state.get("position_mode"),
        )
        self._append_mode_check(
            checks,
            "td_mode",
            request.td_mode,
            exchange_state.get("td_mode"),
        )

        if request.require_trading_enabled:
            trading_enabled = self._trading_enabled(exchange_state)
            checks.append(
                self._readiness_check(
                    "symbol_trading_status",
                    trading_enabled is True,
                    "symbol_trading_enabled",
                    "symbol_trading_disabled",
                    "symbol trading is enabled",
                    "symbol trading is not enabled",
                    metadata={
                        "trading_enabled": trading_enabled,
                        "status": exchange_state.get("trading_status")
                        or exchange_state.get("symbol_status")
                        or exchange_state.get("state"),
                    },
                )
            )

        if request.max_server_time_drift_ms is not None:
            drift_ms = self._server_time_drift_ms(request, exchange_state)
            checks.append(
                self._readiness_check(
                    "server_time_drift",
                    drift_ms is not None and drift_ms <= request.max_server_time_drift_ms,
                    "server_time_drift_within_limit",
                    "server_time_drift_exceeds_limit",
                    "server time drift is within limit",
                    "server time drift is unavailable or exceeds limit",
                    metadata={
                        "drift_ms": drift_ms,
                        "max_server_time_drift_ms": request.max_server_time_drift_ms,
                    },
                )
            )

        spread_bps = self._spread_bps(market_snapshot)
        if request.require_market_snapshot or request.max_spread_bps is not None:
            spread_available = spread_bps is not None
            checks.append(
                self._readiness_check(
                    "market_snapshot",
                    spread_available,
                    "market_snapshot_available",
                    "market_snapshot_missing",
                    "bid/ask market snapshot is available",
                    "bid/ask market snapshot is unavailable",
                    metadata={"spread_bps": spread_bps},
                )
            )
        if request.max_spread_bps is not None and spread_bps is not None:
            checks.append(
                self._readiness_check(
                    "spread_limit",
                    spread_bps <= request.max_spread_bps,
                    "spread_within_limit",
                    "spread_above_limit",
                    "spread is within configured limit",
                    "spread exceeds configured limit",
                    metadata={"spread_bps": spread_bps, "max_spread_bps": request.max_spread_bps},
                )
            )

        slippage_bps = self._float_or_none(
            market_snapshot.get("estimated_slippage_bps")
            or exchange_state.get("estimated_slippage_bps")
        )
        if request.max_slippage_bps is not None:
            checks.append(
                self._readiness_check(
                    "slippage_limit",
                    slippage_bps is not None and slippage_bps <= request.max_slippage_bps,
                    "slippage_within_limit",
                    "slippage_above_limit",
                    "estimated slippage is within configured limit",
                    "estimated slippage is unavailable or exceeds configured limit",
                    metadata={
                        "estimated_slippage_bps": slippage_bps,
                        "max_slippage_bps": request.max_slippage_bps,
                    },
                )
            )

        rate_limit_remaining = self._int_or_none(exchange_state.get("rate_limit_remaining"))
        if request.min_rate_limit_remaining is not None:
            checks.append(
                self._readiness_check(
                    "rate_limit_remaining",
                    rate_limit_remaining is not None
                    and rate_limit_remaining >= request.min_rate_limit_remaining,
                    "rate_limit_capacity_available",
                    "rate_limit_capacity_low",
                    "exchange rate limit capacity is available",
                    "exchange rate limit capacity is unavailable or too low",
                    metadata={
                        "rate_limit_remaining": rate_limit_remaining,
                        "min_rate_limit_remaining": request.min_rate_limit_remaining,
                    },
                )
            )

        reason_codes = [check.code for check in checks if not check.passed and check.severity == "error"]
        approved = not reason_codes
        if approved:
            reason_codes = ["exchange_readiness_approved"]
        return ExchangeReadinessReport(
            report_id=f"{request.request_id}:exchange-readiness",
            broker_name=request.broker_name or self.name,
            symbol=request.symbol,
            checked_at=request.requested_at,
            approved=approved,
            reason_codes=reason_codes,
            checks=checks,
            instrument_rules=instrument_rules,
            market_snapshot={
                **market_snapshot,
                "spread_bps": spread_bps,
            },
            exchange_state=exchange_state,
            metadata={
                **dict(request.metadata or {}),
                "source": "broker_exchange_readiness_v1",
                "live_orders_sent": False,
                "broker_place_order_called": False,
            },
        )

    @abstractmethod
    def get_order(self, client_order_id: str) -> BrokerOrderResult:
        raise NotImplementedError

    @abstractmethod
    def list_open_orders(self, symbol: str | None = None) -> List[BrokerOrderResult]:
        raise NotImplementedError

    def _prepare_exchange_environment(self, request: ExchangeReadinessRequest) -> Dict[str, Any]:
        preparer = getattr(self, "prepare_exchange_environment", None)
        if preparer is None:
            return {}
        prepared = preparer(request)
        return dict(prepared or {})

    def _readiness_instrument_rules(self, symbol: str):
        rules_by_symbol = getattr(self, "instrument_rules", {}) or {}
        rules = rules_by_symbol.get(symbol)
        if rules is not None:
            return rules
        loader = getattr(self, "_rules_for_symbol", None)
        if loader is not None:
            return loader(symbol)
        public_loader = getattr(self, "get_instrument_rules", None)
        if public_loader is not None:
            return public_loader(symbol)
        adapter = getattr(self, "adapter", None)
        adapter_loader = getattr(adapter, "get_instrument_rules", None)
        if adapter_loader is not None:
            return adapter_loader(symbol)
        return None

    @staticmethod
    def _readiness_check(
        name: str,
        passed: bool,
        pass_code: str,
        fail_code: str,
        pass_message: str,
        fail_message: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ExchangeReadinessCheck:
        return ExchangeReadinessCheck(
            name=name,
            passed=bool(passed),
            code=pass_code if passed else fail_code,
            message=pass_message if passed else fail_message,
            severity="info" if passed else "error",
            metadata=dict(metadata or {}),
        )

    @classmethod
    def _append_mode_check(
        cls,
        checks: List[ExchangeReadinessCheck],
        name: str,
        expected: Any,
        actual: Any,
    ) -> None:
        if expected is None:
            return
        expected_value = cls._normalize_mode(expected)
        actual_value = cls._normalize_mode(actual)
        checks.append(
            cls._readiness_check(
                name,
                actual_value == expected_value,
                f"{name}_configured",
                f"{name}_mismatch",
                f"{name} matches requested value",
                f"{name} does not match requested value",
                metadata={"expected": expected_value, "actual": actual_value},
            )
        )

    @staticmethod
    def _normalize_mode(value: Any) -> str:
        return str(getattr(value, "value", value) or "").strip().lower()

    @classmethod
    def _trading_enabled(cls, exchange_state: Dict[str, Any]) -> Optional[bool]:
        if "trading_enabled" in exchange_state:
            return exchange_state.get("trading_enabled") is True
        raw_status = (
            exchange_state.get("trading_status")
            or exchange_state.get("symbol_status")
            or exchange_state.get("state")
        )
        if raw_status is None:
            return None
        status = cls._normalize_mode(raw_status)
        return status in {"trading", "live", "online", "enabled", "open"}

    @staticmethod
    def _server_time_drift_ms(
        request: ExchangeReadinessRequest,
        exchange_state: Dict[str, Any],
    ) -> Optional[int]:
        server_time_ms = BrokerAdapter._int_or_none(exchange_state.get("server_time_ms"))
        if server_time_ms is None:
            server_time_ms = BrokerAdapter._int_or_none(exchange_state.get("exchange_server_time_ms"))
        if server_time_ms is None:
            return None
        local_time_ms = BrokerAdapter._int_or_none(exchange_state.get("local_time_ms"))
        if local_time_ms is None:
            local_time_ms = int(request.requested_at * 1000)
        return abs(server_time_ms - local_time_ms)

    @staticmethod
    def _spread_bps(market_snapshot: Dict[str, Any]) -> Optional[float]:
        bid = BrokerAdapter._float_or_none(
            market_snapshot.get("best_bid")
            or market_snapshot.get("bid")
            or market_snapshot.get("bid_price")
        )
        ask = BrokerAdapter._float_or_none(
            market_snapshot.get("best_ask")
            or market_snapshot.get("ask")
            or market_snapshot.get("ask_price")
        )
        if bid is None or ask is None or bid <= 0.0 or ask <= 0.0 or ask < bid:
            return None
        mid = (bid + ask) / 2.0
        if mid <= 0.0:
            return None
        return ((ask - bid) / mid) * 10000.0

    @staticmethod
    def _float_or_none(value: Any) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _int_or_none(value: Any) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
