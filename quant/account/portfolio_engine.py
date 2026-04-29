from collections import defaultdict
from typing import Dict, Iterable, Tuple

from quant.schemas.portfolio import (
    PortfolioAllocationDecision,
    PortfolioAllocationItem,
    PortfolioAllocationRequest,
    PortfolioOrderRequest,
    PortfolioPositionSnapshot,
)


class PortfolioEngine:
    def allocate(self, request: PortfolioAllocationRequest) -> PortfolioAllocationDecision:
        symbol_notional, strategy_notional, group_notional = self._position_exposure(
            request.positions
        )
        remaining_cash = request.available_cash
        allocations = []
        reason_codes = []

        for order in request.orders:
            item, remaining_cash = self._allocate_order(
                request=request,
                order=order,
                remaining_cash=remaining_cash,
                symbol_notional=symbol_notional,
                strategy_notional=strategy_notional,
                group_notional=group_notional,
            )
            allocations.append(item)
            if item.approved:
                symbol_notional[item.symbol] += item.allocated_notional
                strategy_notional[item.strategy_id] += item.allocated_notional
                if item.correlation_group is not None:
                    group_notional[item.correlation_group] += item.allocated_notional

        allocated_notional = sum(item.allocated_notional for item in allocations)
        if not allocations:
            reason_codes.append("no_orders")
        elif any(item.approved for item in allocations):
            reason_codes.append("portfolio_allocation_approved")
        else:
            reason_codes.append("portfolio_allocation_rejected")

        return PortfolioAllocationDecision(
            allocation_id=request.allocation_id,
            timestamp=request.timestamp,
            approved=any(item.approved for item in allocations),
            account_equity=request.account_equity,
            available_cash=request.available_cash,
            allocated_notional=allocated_notional,
            remaining_cash=remaining_cash,
            allocations=allocations,
            reason_codes=reason_codes,
            trace=request.trace,
        )

    def _allocate_order(
        self,
        request: PortfolioAllocationRequest,
        order: PortfolioOrderRequest,
        remaining_cash: float,
        symbol_notional: Dict[str, float],
        strategy_notional: Dict[str, float],
        group_notional: Dict[str, float],
    ) -> Tuple[PortfolioAllocationItem, float]:
        intent = order.order_intent
        requested_notional = intent.quantity * order.reference_price
        desired_notional = min(
            requested_notional * order.target_weight,
            request.account_equity * order.risk_budget,
        )

        symbol_capacity = self._remaining_capacity(
            request.account_equity,
            request.max_symbol_weight,
            symbol_notional[intent.symbol],
        )
        strategy_capacity = self._remaining_capacity(
            request.account_equity,
            request.max_strategy_weight,
            strategy_notional[order.strategy_id],
        )
        group_capacity = None
        if order.correlation_group is not None:
            group_capacity = self._remaining_capacity(
                request.account_equity,
                request.max_correlation_group_weight,
                group_notional[order.correlation_group],
            )

        caps = [desired_notional, remaining_cash, symbol_capacity, strategy_capacity]
        if group_capacity is not None:
            caps.append(group_capacity)
        allocated_notional = min(caps)

        reason_codes = list(order.reason_codes)
        if allocated_notional < desired_notional:
            reason_codes.append("portfolio_allocation_capped")
        if allocated_notional == symbol_capacity and symbol_capacity < desired_notional:
            reason_codes.append("symbol_risk_budget_capped")
        if allocated_notional == strategy_capacity and strategy_capacity < desired_notional:
            reason_codes.append("strategy_risk_budget_capped")
        if (
            group_capacity is not None
            and allocated_notional == group_capacity
            and group_capacity < desired_notional
        ):
            reason_codes.append("correlation_group_budget_capped")
        if allocated_notional == remaining_cash and remaining_cash < desired_notional:
            reason_codes.append("cash_capped")

        approved = allocated_notional >= request.min_notional and allocated_notional > 0.0
        if approved:
            reason_codes.append("portfolio_order_approved")
            allocated_quantity = allocated_notional / order.reference_price
            next_cash = remaining_cash - allocated_notional
        else:
            reason_codes.append("portfolio_allocation_below_minimum")
            allocated_notional = 0.0
            allocated_quantity = 0.0
            next_cash = remaining_cash

        return (
            PortfolioAllocationItem(
                strategy_id=order.strategy_id,
                client_order_id=intent.client_order_id,
                symbol=intent.symbol,
                side=intent.side,
                approved=approved,
                requested_quantity=intent.quantity,
                allocated_quantity=allocated_quantity,
                requested_notional=requested_notional,
                allocated_notional=allocated_notional,
                reference_price=order.reference_price,
                correlation_group=order.correlation_group,
                reason_codes=reason_codes,
                trace=intent.trace,
            ),
            next_cash,
        )

    def _position_exposure(
        self, positions: Iterable[PortfolioPositionSnapshot]
    ) -> Tuple[Dict[str, float], Dict[str, float], Dict[str, float]]:
        symbol_notional: Dict[str, float] = defaultdict(float)
        strategy_notional: Dict[str, float] = defaultdict(float)
        group_notional: Dict[str, float] = defaultdict(float)
        for position in positions:
            notional = position.notional
            symbol_notional[position.symbol] += notional
            strategy_notional[position.strategy_id] += notional
            if position.correlation_group is not None:
                group_notional[position.correlation_group] += notional
        return symbol_notional, strategy_notional, group_notional

    def _remaining_capacity(
        self,
        account_equity: float,
        max_weight: float,
        current_notional: float,
    ) -> float:
        return max(0.0, account_equity * max_weight - current_notional)
