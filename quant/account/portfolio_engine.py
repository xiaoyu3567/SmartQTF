from collections import defaultdict
from typing import Dict, Iterable, List, Tuple

from quant.schemas.enums import TradeSide
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
        side_notional = self._position_side_exposure(request.positions)
        remaining_cash = request.available_cash
        allocations_by_index = {}
        reason_codes = []
        indexed_orders = list(enumerate(request.orders))

        reduce_only_orders = [
            (index, order)
            for index, order in indexed_orders
            if order.order_intent.reduce_only
        ]
        open_orders = [
            (index, order)
            for index, order in indexed_orders
            if not order.order_intent.reduce_only
        ]

        for index, order in reduce_only_orders:
            item = self._allocate_reduce_only_order(
                request=request,
                order=order,
                side_notional=side_notional,
            )
            allocations_by_index[index] = item
            if item.approved:
                self._apply_reduce_only_allocation(
                    item=item,
                    strategy_id=order.strategy_id,
                    symbol_notional=symbol_notional,
                    strategy_notional=strategy_notional,
                    group_notional=group_notional,
                    side_notional=side_notional,
                )

        for symbol_orders in self._group_open_orders_by_symbol(open_orders).values():
            allocated_items, remaining_cash = self._allocate_symbol_open_orders(
                request=request,
                indexed_orders=symbol_orders,
                remaining_cash=remaining_cash,
                symbol_notional=symbol_notional,
                strategy_notional=strategy_notional,
                group_notional=group_notional,
            )
            for index, item in allocated_items:
                allocations_by_index[index] = item
                if item.approved:
                    symbol_notional[item.symbol] += item.allocated_notional
                    strategy_notional[item.strategy_id] += item.allocated_notional
                    if item.correlation_group is not None:
                        group_notional[item.correlation_group] += item.allocated_notional

        allocations = [
            allocations_by_index[index]
            for index, _order in indexed_orders
            if index in allocations_by_index
        ]

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

    def _allocate_reduce_only_order(
        self,
        request: PortfolioAllocationRequest,
        order: PortfolioOrderRequest,
        side_notional: Dict[str, Dict[TradeSide, float]],
    ) -> PortfolioAllocationItem:
        intent = order.order_intent
        requested_notional = self._requested_notional(order)
        close_side = self._opposite_side(intent.side)
        matching_notional = side_notional[intent.symbol][close_side]
        allocated_notional = min(requested_notional, matching_notional)

        reason_codes = list(order.reason_codes)
        reason_codes.append("portfolio_reduce_only_priority")
        if allocated_notional < requested_notional:
            reason_codes.append("reduce_only_position_capped")

        approved = allocated_notional >= request.min_notional and allocated_notional > 0.0
        if approved:
            reason_codes.append("portfolio_order_approved")
            allocated_quantity = allocated_notional / order.reference_price
        else:
            allocated_notional = 0.0
            allocated_quantity = 0.0
            if matching_notional <= 0.0:
                reason_codes.append("reduce_only_no_matching_position")
            reason_codes.append("portfolio_allocation_below_minimum")

        return PortfolioAllocationItem(
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
        )

    def _allocate_symbol_open_orders(
        self,
        request: PortfolioAllocationRequest,
        indexed_orders: List[Tuple[int, PortfolioOrderRequest]],
        remaining_cash: float,
        symbol_notional: Dict[str, float],
        strategy_notional: Dict[str, float],
        group_notional: Dict[str, float],
    ) -> Tuple[List[Tuple[int, PortfolioAllocationItem]], float]:
        by_side: Dict[TradeSide, List[Tuple[int, PortfolioOrderRequest]]] = defaultdict(list)
        for index, order in indexed_orders:
            by_side[order.order_intent.side].append((index, order))

        if len(by_side) <= 1:
            return self._allocate_same_direction_group(
                request=request,
                indexed_orders=indexed_orders,
                remaining_cash=remaining_cash,
                symbol_notional=symbol_notional,
                strategy_notional=strategy_notional,
                group_notional=group_notional,
            )

        side_desired = {
            side: sum(self._desired_notional(request, order) for _index, order in orders)
            for side, orders in by_side.items()
        }
        dominant_side = max(side_desired, key=side_desired.get)
        dominant_desired = side_desired[dominant_side]
        offset_notional = sum(
            desired for side, desired in side_desired.items() if side != dominant_side
        )

        if dominant_desired <= offset_notional or dominant_desired <= 0.0:
            rejected = []
            for index, order in indexed_orders:
                rejected.append(
                    (
                        index,
                        self._reject_order(
                            order,
                            [
                                "portfolio_opposite_side_conflict",
                                "portfolio_allocation_below_minimum",
                            ],
                        ),
                    )
                )
            return rejected, remaining_cash

        net_factor = (dominant_desired - offset_notional) / dominant_desired
        rejected = [
            (
                index,
                self._reject_order(
                    order,
                    [
                        "portfolio_opposite_side_offset",
                        "portfolio_allocation_below_minimum",
                    ],
                ),
            )
            for side, orders in by_side.items()
            if side != dominant_side
            for index, order in orders
        ]
        allocated, remaining_cash = self._allocate_same_direction_group(
            request=request,
            indexed_orders=by_side[dominant_side],
            remaining_cash=remaining_cash,
            symbol_notional=symbol_notional,
            strategy_notional=strategy_notional,
            group_notional=group_notional,
            net_factor=net_factor,
            extra_reason_codes=["portfolio_opposite_side_netting_applied"],
        )
        return rejected + allocated, remaining_cash

    def _allocate_same_direction_group(
        self,
        request: PortfolioAllocationRequest,
        indexed_orders: List[Tuple[int, PortfolioOrderRequest]],
        remaining_cash: float,
        symbol_notional: Dict[str, float],
        strategy_notional: Dict[str, float],
        group_notional: Dict[str, float],
        net_factor: float = 1.0,
        extra_reason_codes: List[str] = None,
    ) -> Tuple[List[Tuple[int, PortfolioAllocationItem]], float]:
        if not indexed_orders:
            return [], remaining_cash

        extra_reason_codes = extra_reason_codes or []
        allocations = {
            index: self._desired_notional(request, order) * net_factor
            for index, order in indexed_orders
        }
        desired_allocations = dict(allocations)
        cap_reasons: Dict[int, set] = defaultdict(set)

        self._scale_by_strategy_capacity(
            indexed_orders,
            allocations,
            desired_allocations,
            request.account_equity,
            request.max_strategy_weight,
            strategy_notional,
            cap_reasons,
        )
        self._scale_by_group_capacity(
            indexed_orders,
            allocations,
            desired_allocations,
            request.account_equity,
            request.max_correlation_group_weight,
            group_notional,
            cap_reasons,
        )
        self._scale_total_capacity(
            indexed_orders,
            allocations,
            desired_allocations,
            self._remaining_capacity(
                request.account_equity,
                request.max_symbol_weight,
                symbol_notional[indexed_orders[0][1].order_intent.symbol],
            ),
            "symbol_risk_budget_capped",
            cap_reasons,
        )
        self._scale_total_capacity(
            indexed_orders,
            allocations,
            desired_allocations,
            remaining_cash,
            "cash_capped",
            cap_reasons,
        )

        group_reason_codes = list(extra_reason_codes)
        if len(indexed_orders) > 1:
            group_reason_codes.append("portfolio_same_direction_merged")

        items = []
        total_allocated = 0.0
        for index, order in indexed_orders:
            desired_notional = self._desired_notional(request, order) * net_factor
            allocated_notional = allocations[index]
            item = self._allocation_item_from_notional(
                request=request,
                order=order,
                desired_notional=desired_notional,
                allocated_notional=allocated_notional,
                extra_reason_codes=group_reason_codes,
                cap_reasons=cap_reasons[index],
            )
            items.append((index, item))
            if item.approved:
                total_allocated += item.allocated_notional

        return items, remaining_cash - total_allocated

    def _allocation_item_from_notional(
        self,
        request: PortfolioAllocationRequest,
        order: PortfolioOrderRequest,
        desired_notional: float,
        allocated_notional: float,
        extra_reason_codes: List[str],
        cap_reasons: Iterable[str],
    ) -> PortfolioAllocationItem:
        intent = order.order_intent
        requested_notional = self._requested_notional(order)

        reason_codes = list(order.reason_codes)
        reason_codes.extend(extra_reason_codes)
        if allocated_notional < desired_notional:
            reason_codes.append("portfolio_allocation_capped")
        for reason_code in sorted(cap_reasons):
            reason_codes.append(reason_code)

        approved = allocated_notional >= request.min_notional and allocated_notional > 0.0
        if approved:
            reason_codes.append("portfolio_order_approved")
            allocated_quantity = allocated_notional / order.reference_price
        else:
            reason_codes.append("portfolio_allocation_below_minimum")
            allocated_notional = 0.0
            allocated_quantity = 0.0

        return PortfolioAllocationItem(
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

    def _position_side_exposure(
        self, positions: Iterable[PortfolioPositionSnapshot]
    ) -> Dict[str, Dict[TradeSide, float]]:
        side_notional: Dict[str, Dict[TradeSide, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        for position in positions:
            side_notional[position.symbol][position.side] += position.notional
        return side_notional

    def _remaining_capacity(
        self,
        account_equity: float,
        max_weight: float,
        current_notional: float,
    ) -> float:
        return max(0.0, account_equity * max_weight - current_notional)

    def _requested_notional(self, order: PortfolioOrderRequest) -> float:
        return order.order_intent.quantity * order.reference_price

    def _desired_notional(
        self, request: PortfolioAllocationRequest, order: PortfolioOrderRequest
    ) -> float:
        requested_notional = self._requested_notional(order)
        return min(
            requested_notional * order.target_weight,
            request.account_equity * order.risk_budget,
        )

    def _group_open_orders_by_symbol(
        self, indexed_orders: List[Tuple[int, PortfolioOrderRequest]]
    ) -> Dict[str, List[Tuple[int, PortfolioOrderRequest]]]:
        orders_by_symbol: Dict[str, List[Tuple[int, PortfolioOrderRequest]]] = defaultdict(list)
        for index, order in indexed_orders:
            orders_by_symbol[order.order_intent.symbol].append((index, order))
        return orders_by_symbol

    def _reject_order(
        self, order: PortfolioOrderRequest, reason_codes: List[str]
    ) -> PortfolioAllocationItem:
        intent = order.order_intent
        requested_notional = self._requested_notional(order)
        return PortfolioAllocationItem(
            strategy_id=order.strategy_id,
            client_order_id=intent.client_order_id,
            symbol=intent.symbol,
            side=intent.side,
            approved=False,
            requested_quantity=intent.quantity,
            allocated_quantity=0.0,
            requested_notional=requested_notional,
            allocated_notional=0.0,
            reference_price=order.reference_price,
            correlation_group=order.correlation_group,
            reason_codes=list(order.reason_codes) + reason_codes,
            trace=intent.trace,
        )

    def _apply_reduce_only_allocation(
        self,
        item: PortfolioAllocationItem,
        strategy_id: str,
        symbol_notional: Dict[str, float],
        strategy_notional: Dict[str, float],
        group_notional: Dict[str, float],
        side_notional: Dict[str, Dict[TradeSide, float]],
    ) -> None:
        close_side = self._opposite_side(item.side)
        side_notional[item.symbol][close_side] = max(
            0.0, side_notional[item.symbol][close_side] - item.allocated_notional
        )
        symbol_notional[item.symbol] = max(
            0.0, symbol_notional[item.symbol] - item.allocated_notional
        )
        strategy_notional[strategy_id] = max(
            0.0, strategy_notional[strategy_id] - item.allocated_notional
        )
        if item.correlation_group is not None:
            group_notional[item.correlation_group] = max(
                0.0, group_notional[item.correlation_group] - item.allocated_notional
            )

    def _opposite_side(self, side: TradeSide) -> TradeSide:
        return TradeSide.SELL if side == TradeSide.BUY else TradeSide.BUY

    def _scale_by_strategy_capacity(
        self,
        indexed_orders: List[Tuple[int, PortfolioOrderRequest]],
        allocations: Dict[int, float],
        desired_allocations: Dict[int, float],
        account_equity: float,
        max_weight: float,
        strategy_notional: Dict[str, float],
        cap_reasons: Dict[int, set],
    ) -> None:
        by_strategy: Dict[str, List[int]] = defaultdict(list)
        for index, order in indexed_orders:
            by_strategy[order.strategy_id].append(index)

        for strategy_id, indexes in by_strategy.items():
            capacity = self._remaining_capacity(
                account_equity,
                max_weight,
                strategy_notional[strategy_id],
            )
            self._scale_indexes(
                indexes,
                allocations,
                desired_allocations,
                capacity,
                "strategy_risk_budget_capped",
                cap_reasons,
            )

    def _scale_by_group_capacity(
        self,
        indexed_orders: List[Tuple[int, PortfolioOrderRequest]],
        allocations: Dict[int, float],
        desired_allocations: Dict[int, float],
        account_equity: float,
        max_weight: float,
        group_notional: Dict[str, float],
        cap_reasons: Dict[int, set],
    ) -> None:
        by_group: Dict[str, List[int]] = defaultdict(list)
        for index, order in indexed_orders:
            if order.correlation_group is not None:
                by_group[order.correlation_group].append(index)

        for correlation_group, indexes in by_group.items():
            capacity = self._remaining_capacity(
                account_equity,
                max_weight,
                group_notional[correlation_group],
            )
            self._scale_indexes(
                indexes,
                allocations,
                desired_allocations,
                capacity,
                "correlation_group_budget_capped",
                cap_reasons,
            )

    def _scale_total_capacity(
        self,
        indexed_orders: List[Tuple[int, PortfolioOrderRequest]],
        allocations: Dict[int, float],
        desired_allocations: Dict[int, float],
        capacity: float,
        reason_code: str,
        cap_reasons: Dict[int, set],
    ) -> None:
        self._scale_indexes(
            [index for index, _order in indexed_orders],
            allocations,
            desired_allocations,
            capacity,
            reason_code,
            cap_reasons,
        )

    def _scale_indexes(
        self,
        indexes: List[int],
        allocations: Dict[int, float],
        desired_allocations: Dict[int, float],
        capacity: float,
        reason_code: str,
        cap_reasons: Dict[int, set],
    ) -> None:
        total = sum(allocations[index] for index in indexes)
        desired_total = sum(desired_allocations[index] for index in indexes)
        if desired_total > capacity:
            for index in indexes:
                if desired_allocations[index] > 0.0:
                    cap_reasons[index].add(reason_code)
        if total <= capacity:
            return

        scale = 0.0 if total <= 0.0 else max(0.0, capacity) / total
        for index in indexes:
            if allocations[index] > 0.0:
                allocations[index] *= scale
