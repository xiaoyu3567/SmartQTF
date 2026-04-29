from collections import defaultdict
from typing import Iterable, Optional

from quant.schemas import (
    AttributionBucket,
    DecisionLogRecord,
    FillLogRecord,
    TradeAttributionReport,
)


class TradeAttributionAnalyzer:
    def build_report(
        self,
        records: Iterable[object],
        report_id: str,
        run_id: Optional[str] = None,
        generated_at: Optional[int] = None,
    ) -> TradeAttributionReport:
        records = list(records)
        decisions = {
            record.decision.decision_id: record.decision
            for record in records
            if isinstance(record, DecisionLogRecord)
        }
        fills = [record for record in records if isinstance(record, FillLogRecord)]

        if run_id is None:
            run_id = self._infer_run_id(records)
        if generated_at is None:
            generated_at = max([record.timestamp for record in records], default=0)

        buckets = {}
        total_gross_pnl = 0.0
        total_fees = 0.0
        total_net_pnl = 0.0
        trade_count = 0

        for fill in fills:
            decision = decisions.get(fill.decision_id)
            net_pnl = self._realized_pnl(fill)
            fees = self._fees(fill)
            gross_pnl = net_pnl + fees
            total_gross_pnl += gross_pnl
            total_fees += fees
            total_net_pnl += net_pnl
            trade_count += 1 if net_pnl != 0.0 else 0

            for bucket_type, bucket_values in self._bucket_values(fill, decision).items():
                for bucket_value in bucket_values:
                    key = (bucket_type, bucket_value)
                    bucket = buckets.setdefault(
                        key,
                        _MutableBucket(bucket_type=bucket_type, bucket_value=bucket_value),
                    )
                    bucket.add(gross_pnl=gross_pnl, fees=fees, net_pnl=net_pnl)

        return TradeAttributionReport(
            report_id=report_id,
            run_id=run_id,
            generated_at=generated_at,
            buckets=[
                bucket.to_schema()
                for bucket in sorted(
                    buckets.values(),
                    key=lambda value: (value.bucket_type, value.bucket_value),
                )
            ],
            total_gross_pnl=total_gross_pnl,
            total_fees=total_fees,
            total_net_pnl=total_net_pnl,
            fill_count=len(fills),
            trade_count=trade_count,
        )

    def _infer_run_id(self, records):
        for record in records:
            run_id = getattr(record, "run_id", None)
            if run_id is not None:
                return run_id
        return "unknown"

    def _realized_pnl(self, fill):
        return float(fill.metadata.get("realized_pnl", 0.0))

    def _fees(self, fill):
        return float(fill.metadata.get("fee", fill.commission))

    def _bucket_values(self, fill, decision):
        strategy_id = fill.metadata.get("strategy_id")
        regime = fill.metadata.get("regime")
        reason_codes = fill.metadata.get("reason_codes")

        if decision is not None:
            strategy_id = strategy_id or decision.strategy_id
            regime = regime or decision.regime
            reason_codes = reason_codes or decision.reason_codes

        if isinstance(reason_codes, str):
            reason_codes = [reason_codes]

        return {
            "symbol": [fill.symbol],
            "strategy": [strategy_id or "unknown"],
            "regime": [regime or "unknown"],
            "rule": list(reason_codes or ["unknown"]),
        }


class _MutableBucket:
    def __init__(self, bucket_type, bucket_value):
        self.bucket_type = bucket_type
        self.bucket_value = bucket_value
        self.gross_pnl = 0.0
        self.fees = 0.0
        self.net_pnl = 0.0
        self.fill_count = 0
        self.trade_count = 0
        self.winning_trades = 0
        self.losing_trades = 0

    def add(self, gross_pnl, fees, net_pnl):
        self.gross_pnl += gross_pnl
        self.fees += fees
        self.net_pnl += net_pnl
        self.fill_count += 1
        if net_pnl > 0.0:
            self.trade_count += 1
            self.winning_trades += 1
        elif net_pnl < 0.0:
            self.trade_count += 1
            self.losing_trades += 1

    def to_schema(self):
        return AttributionBucket(
            bucket_type=self.bucket_type,
            bucket_value=self.bucket_value,
            gross_pnl=self.gross_pnl,
            fees=self.fees,
            net_pnl=self.net_pnl,
            fill_count=self.fill_count,
            trade_count=self.trade_count,
            winning_trades=self.winning_trades,
            losing_trades=self.losing_trades,
        )
