from collections import defaultdict
from typing import Any, Iterable, Optional

from quant.logging.jsonl import JsonlTradeLogger
from quant.schemas.enums import DecisionAction, OrderStatus, TradeSide
from quant.schemas.logging import (
    DecisionLogRecord,
    FillLogRecord,
    OrderLogRecord,
    PortfolioAllocationLogRecord,
    RegimeLogRecord,
    RiskDecisionLogRecord,
    TradeJournalEntry,
    TradeJournalFill,
    TradeJournalOrder,
)


class TradeJournalReconstructor:
    def reconstruct_from_logger(self, logger: JsonlTradeLogger) -> list[TradeJournalEntry]:
        return self.reconstruct(logger.read_all())

    def reconstruct(self, records: Iterable[object]) -> list[TradeJournalEntry]:
        groups = defaultdict(list)
        for record in sorted(records, key=lambda item: getattr(item, "timestamp", 0)):
            trade_id = self._trade_id(record)
            if trade_id is None:
                continue
            groups[trade_id].append(record)

        return [
            self._build_entry(trade_id, grouped_records)
            for trade_id, grouped_records in sorted(groups.items())
        ]

    def _build_entry(self, trade_id: str, records: list[object]) -> TradeJournalEntry:
        decisions = [record for record in records if isinstance(record, DecisionLogRecord)]
        risks = [record for record in records if isinstance(record, RiskDecisionLogRecord)]
        portfolios = [record for record in records if isinstance(record, PortfolioAllocationLogRecord)]
        orders = [record for record in records if isinstance(record, OrderLogRecord)]
        fills = [record for record in records if isinstance(record, FillLogRecord)]
        regimes = [record for record in records if isinstance(record, RegimeLogRecord)]

        decision_by_id = {
            record.decision.decision_id: record.decision
            for record in decisions
        }
        entry_fills, exit_fills = self._split_fills(fills, decision_by_id)
        entry_side = self._entry_side(entry_fills, fills, decisions)
        exit_side = self._exit_side(exit_fills)
        entry_avg_price = self._weighted_avg(entry_fills)
        exit_avg_price = self._weighted_avg(exit_fills)
        entry_quantity = self._quantity(entry_fills)
        exit_quantity = self._quantity(exit_fills)
        entry_notional = self._notional(entry_fills)
        exit_notional = self._notional(exit_fills)
        fees = self._fees(fills)
        gross_pnl, net_pnl, pnl_source = self._pnl(
            fills=fills,
            entry_side=entry_side,
            entry_quantity=entry_quantity,
            entry_avg_price=entry_avg_price,
            exit_quantity=exit_quantity,
            exit_avg_price=exit_avg_price,
            fees=fees,
        )
        expected_entry_price = self._expected_price(entry_fills, orders, decision_by_id)
        expected_exit_price = self._expected_price(exit_fills, orders, decision_by_id)
        risk_approved = self._risk_approved(risks)
        feature_snapshot = self._feature_snapshot(decisions)
        regime_snapshot = regimes[0].regime_snapshot if regimes else None

        return TradeJournalEntry(
            journal_id=f"journal:{trade_id}",
            run_id=self._run_id(records),
            trade_id=trade_id,
            symbol=self._symbol(records),
            status=self._status(
                risk_approved=risk_approved,
                orders=orders,
                entry_quantity=entry_quantity,
                exit_quantity=exit_quantity,
            ),
            strategy_id=self._strategy_id(decisions, orders, fills),
            strategy_version=self._strategy_version(decisions),
            regime=self._regime(decisions, regimes),
            regime_snapshot_id=None if regime_snapshot is None else regime_snapshot.regime_id,
            feature_snapshot_id=self._feature_snapshot_id(feature_snapshot),
            feature_values=self._feature_values(feature_snapshot),
            decision_ids=self._decision_ids(records, decisions),
            order_ids=self._order_ids(orders),
            client_order_ids=self._client_order_ids(records, orders, fills),
            risk_decision_ids=self._risk_decision_ids(risks, orders, fills),
            allocation_ids=self._allocation_ids(portfolios, orders, fills),
            decision_reason_codes=self._decision_reason_codes(decisions, orders, fills),
            risk_approved=risk_approved,
            risk_reason_codes=self._risk_reason_codes(risks),
            entry_side=entry_side,
            exit_side=exit_side,
            entry_quantity=entry_quantity,
            exit_quantity=exit_quantity,
            entry_avg_price=entry_avg_price,
            exit_avg_price=exit_avg_price,
            entry_notional=entry_notional,
            exit_notional=exit_notional,
            expected_entry_price=expected_entry_price,
            expected_exit_price=expected_exit_price,
            entry_slippage=self._slippage(entry_side, entry_avg_price, expected_entry_price),
            exit_slippage=self._slippage(exit_side, exit_avg_price, expected_exit_price),
            fees=fees,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            realized_pnl_source=pnl_source,
            orders=[self._journal_order(order) for order in orders],
            fills=[self._journal_fill(fill) for fill in fills],
            timeline=self._timeline(records),
            metadata={
                "source_record_count": len(records),
                "decision_count": len(decisions),
                "order_count": len(orders),
                "fill_count": len(fills),
                "risk_record_count": len(risks),
                "portfolio_record_count": len(portfolios),
                "regime_record_count": len(regimes),
            },
        )

    def _split_fills(self, fills, decision_by_id):
        entry_fills = []
        exit_fills = []
        first_side = None
        for fill in fills:
            leg = self._metadata(fill).get("trade_leg") or self._metadata(fill).get("leg")
            if leg in {"entry", "exit"}:
                target = entry_fills if leg == "entry" else exit_fills
                target.append(fill)
                continue

            action = self._decision_action(decision_by_id.get(fill.decision_id))
            if action in {DecisionAction.OPEN_LONG.value, DecisionAction.OPEN_SHORT.value}:
                entry_fills.append(fill)
                continue
            if action in {DecisionAction.CLOSE_LONG.value, DecisionAction.CLOSE_SHORT.value}:
                exit_fills.append(fill)
                continue

            side = self._value(fill.side)
            if first_side is None:
                first_side = side
            if side == first_side:
                entry_fills.append(fill)
            else:
                exit_fills.append(fill)
        return entry_fills, exit_fills

    def _pnl(
        self,
        fills,
        entry_side,
        entry_quantity,
        entry_avg_price,
        exit_quantity,
        exit_avg_price,
        fees,
    ):
        explicit = [
            float(self._metadata(fill)["realized_pnl"])
            for fill in fills
            if "realized_pnl" in self._metadata(fill)
        ]
        if explicit:
            net_pnl = sum(explicit)
            return net_pnl + fees, net_pnl, "fill_metadata_realized_pnl"

        if not entry_avg_price or not exit_avg_price or entry_quantity <= 0.0 or exit_quantity <= 0.0:
            return 0.0, 0.0, "insufficient_fills"

        matched_quantity = min(entry_quantity, exit_quantity)
        direction = 1.0 if self._value(entry_side) == TradeSide.BUY.value else -1.0
        gross_pnl = (exit_avg_price - entry_avg_price) * matched_quantity * direction
        return gross_pnl, gross_pnl - fees, "calculated_from_fills"

    def _expected_price(self, fills, orders, decision_by_id):
        if not fills:
            return None
        fill = fills[0]
        for order in orders:
            if order.client_order_id == fill.client_order_id and order.price is not None:
                return order.price
        decision = decision_by_id.get(fill.decision_id)
        return None if decision is None else decision.limit_price

    def _slippage(self, side, avg_price, expected_price):
        if side is None or avg_price is None or expected_price is None:
            return None
        if self._value(side) == TradeSide.BUY.value:
            return avg_price - expected_price
        return expected_price - avg_price

    def _trade_id(self, record) -> Optional[str]:
        metadata = self._metadata(record)
        for key in ("trade_id", "journal_trade_id"):
            if metadata.get(key):
                return str(metadata[key])
        context = metadata.get("portfolio_execution_context")
        if isinstance(context, dict) and context.get("trade_id"):
            return str(context["trade_id"])
        if isinstance(record, DecisionLogRecord):
            return record.decision.decision_id
        if isinstance(record, (RiskDecisionLogRecord, OrderLogRecord, FillLogRecord)):
            return getattr(record, "decision_id", None) or getattr(record, "client_order_id", None)
        if isinstance(record, PortfolioAllocationLogRecord):
            return record.decision_id or record.allocation.allocation_id
        if isinstance(record, RegimeLogRecord):
            return metadata.get("decision_id")
        return None

    def _feature_snapshot(self, decision_records):
        for record in decision_records:
            if record.feature_snapshot is not None:
                return record.feature_snapshot
            snapshot = self._metadata(record).get("feature_snapshot") or self._metadata(record).get("features")
            if snapshot:
                return snapshot
        return None

    def _feature_snapshot_id(self, snapshot):
        if snapshot is None:
            return None
        if hasattr(snapshot, "snapshot_id"):
            return snapshot.snapshot_id
        if isinstance(snapshot, dict):
            return snapshot.get("snapshot_id")
        return None

    def _feature_values(self, snapshot):
        if snapshot is None:
            return {}
        if hasattr(snapshot, "values"):
            return dict(snapshot.values)
        if isinstance(snapshot, dict):
            values = snapshot.get("values", snapshot)
            return dict(values) if isinstance(values, dict) else {}
        return {}

    def _status(self, risk_approved, orders, entry_quantity, exit_quantity):
        if risk_approved is False and entry_quantity <= 0.0:
            return "rejected"
        if any(self._value(order.status) == OrderStatus.REJECTED.value for order in orders) and entry_quantity <= 0.0:
            return "rejected"
        if entry_quantity > 0.0 and exit_quantity >= entry_quantity:
            return "closed"
        if entry_quantity > 0.0:
            return "open"
        return "unknown"

    def _entry_side(self, entry_fills, fills, decisions):
        if entry_fills:
            return entry_fills[0].side
        for record in decisions:
            action = self._decision_action(record.decision)
            if action == DecisionAction.OPEN_LONG.value:
                return TradeSide.BUY
            if action == DecisionAction.OPEN_SHORT.value:
                return TradeSide.SELL
        return fills[0].side if fills else None

    def _exit_side(self, exit_fills):
        return exit_fills[0].side if exit_fills else None

    def _risk_approved(self, risks):
        if not risks:
            return None
        if any(record.approved is False for record in risks):
            return False
        return True

    def _journal_order(self, order):
        return TradeJournalOrder(
            timestamp=order.timestamp,
            order_id=order.order_id,
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            status=order.status,
            quantity=order.quantity,
            filled_quantity=order.filled_quantity,
            remaining_quantity=order.remaining_quantity,
            price=order.price,
            decision_id=order.decision_id,
            metadata=dict(order.metadata),
        )

    def _journal_fill(self, fill):
        return TradeJournalFill(
            timestamp=fill.timestamp,
            fill_id=fill.fill_id,
            order_id=fill.order_id,
            client_order_id=fill.client_order_id,
            symbol=fill.symbol,
            side=fill.side,
            filled_quantity=fill.filled_quantity,
            fill_price=fill.fill_price,
            commission=fill.commission,
            decision_id=fill.decision_id,
            metadata=dict(fill.metadata),
        )

    def _timeline(self, records):
        items = []
        for record in records:
            item = {
                "timestamp": getattr(record, "timestamp", 0),
                "record_type": getattr(record, "record_type", record.__class__.__name__),
                "event_id": getattr(record, "event_id", None),
            }
            if isinstance(record, DecisionLogRecord):
                item["decision_id"] = record.decision.decision_id
            elif isinstance(record, (RiskDecisionLogRecord, OrderLogRecord, FillLogRecord)):
                item["decision_id"] = getattr(record, "decision_id", None)
            elif isinstance(record, PortfolioAllocationLogRecord):
                item["allocation_id"] = record.allocation.allocation_id
            elif isinstance(record, RegimeLogRecord):
                item["regime_id"] = record.regime_snapshot.regime_id
            items.append(item)
        return sorted(items, key=lambda item: item["timestamp"])

    def _run_id(self, records):
        for record in records:
            run_id = getattr(record, "run_id", None)
            if run_id:
                return run_id
        return "unknown"

    def _symbol(self, records):
        for record in records:
            if isinstance(record, DecisionLogRecord):
                return record.decision.symbol
            if isinstance(record, (RiskDecisionLogRecord, OrderLogRecord, FillLogRecord)):
                return record.symbol
            if isinstance(record, PortfolioAllocationLogRecord):
                return record.allocation.allocations[0].symbol if record.allocation.allocations else "unknown"
            if isinstance(record, RegimeLogRecord):
                return record.regime_snapshot.symbol
        return "unknown"

    def _strategy_id(self, decisions, orders, fills):
        for record in decisions:
            if record.decision.strategy_id:
                return record.decision.strategy_id
        for record in orders + fills:
            value = self._metadata(record).get("strategy_id")
            if value:
                return value
        return None

    def _strategy_version(self, decisions):
        for record in decisions:
            if record.decision.strategy_version:
                return record.decision.strategy_version
        return None

    def _regime(self, decisions, regimes):
        for record in decisions:
            if record.decision.regime:
                return str(record.decision.regime)
        if regimes:
            return str(self._value(regimes[0].regime_snapshot.regime))
        return None

    def _decision_ids(self, records, decisions):
        values = [record.decision.decision_id for record in decisions]
        for record in records:
            decision_id = getattr(record, "decision_id", None)
            if decision_id:
                values.append(decision_id)
        return self._unique(values)

    def _order_ids(self, orders):
        return self._unique([order.order_id for order in orders])

    def _client_order_ids(self, records, orders, fills):
        values = [order.client_order_id for order in orders] + [fill.client_order_id for fill in fills]
        for record in records:
            context = self._metadata(record).get("portfolio_execution_context")
            if isinstance(context, dict) and context.get("client_order_id"):
                values.append(context["client_order_id"])
        return self._unique(values)

    def _risk_decision_ids(self, risks, orders, fills):
        values = []
        for record in risks:
            if record.risk_decision.risk_decision_id:
                values.append(record.risk_decision.risk_decision_id)
            if self._metadata(record).get("risk_decision_id"):
                values.append(self._metadata(record)["risk_decision_id"])
        for record in orders + fills:
            metadata = self._metadata(record)
            if metadata.get("risk_decision_id"):
                values.append(metadata["risk_decision_id"])
            context = metadata.get("portfolio_execution_context")
            if isinstance(context, dict) and context.get("risk_decision_id"):
                values.append(context["risk_decision_id"])
        return self._unique(values)

    def _allocation_ids(self, portfolios, orders, fills):
        values = [record.allocation.allocation_id for record in portfolios]
        for record in orders + fills:
            metadata = self._metadata(record)
            for key in ("allocation_id", "portfolio_allocation_id"):
                if metadata.get(key):
                    values.append(metadata[key])
            context = metadata.get("portfolio_execution_context")
            if isinstance(context, dict):
                allocation_id = context.get("allocation_id") or context.get("portfolio_allocation_id")
                if allocation_id:
                    values.append(allocation_id)
        return self._unique(values)

    def _decision_reason_codes(self, decisions, orders, fills):
        values = []
        for record in decisions:
            values.extend(record.decision.reason_codes)
        for record in orders + fills:
            reason_codes = self._metadata(record).get("reason_codes")
            if isinstance(reason_codes, str):
                values.append(reason_codes)
            elif isinstance(reason_codes, list):
                values.extend(reason_codes)
        return self._unique(values)

    def _risk_reason_codes(self, risks):
        values = []
        for record in risks:
            values.extend(record.reason_codes)
            values.extend(record.risk_decision.reason_codes)
        return self._unique(values)

    def _decision_action(self, decision):
        return None if decision is None else self._value(decision.action)

    def _quantity(self, fills):
        return sum(float(fill.filled_quantity) for fill in fills)

    def _notional(self, fills):
        return sum(float(fill.filled_quantity) * float(fill.fill_price) for fill in fills)

    def _weighted_avg(self, fills):
        quantity = self._quantity(fills)
        if quantity <= 0.0:
            return None
        return self._notional(fills) / quantity

    def _fees(self, fills):
        total = 0.0
        for fill in fills:
            metadata = self._metadata(fill)
            total += float(metadata.get("fee", fill.commission))
        return total

    def _metadata(self, record):
        metadata = getattr(record, "metadata", None)
        return metadata if isinstance(metadata, dict) else {}

    def _value(self, value: Any):
        return getattr(value, "value", value)

    def _unique(self, values):
        seen = set()
        result = []
        for value in values:
            if value is None:
                continue
            string_value = str(value)
            if string_value in seen:
                continue
            seen.add(string_value)
            result.append(string_value)
        return result
