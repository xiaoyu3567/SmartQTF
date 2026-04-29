from datetime import datetime, timezone
from math import sqrt
from typing import Iterable, Optional

from quant.schemas import (
    DailyReviewBucket,
    DailyReviewReport,
    DecisionLogRecord,
    FillLogRecord,
    OrderLogRecord,
    OrderStatus,
)


class DailyReviewReporter:
    def build_report(
        self,
        records: Iterable[object],
        report_id: str,
        run_id: Optional[str] = None,
        trading_date: Optional[str] = None,
        generated_at: Optional[int] = None,
    ) -> DailyReviewReport:
        records = list(records)
        decisions = {
            record.decision.decision_id: record.decision
            for record in records
            if isinstance(record, DecisionLogRecord)
        }
        decision_records = {
            record.decision.decision_id: record
            for record in records
            if isinstance(record, DecisionLogRecord)
        }
        fills = [record for record in records if isinstance(record, FillLogRecord)]
        rejected_orders = [
            record
            for record in records
            if isinstance(record, OrderLogRecord) and record.status == OrderStatus.REJECTED
        ]

        if run_id is None:
            run_id = self._infer_run_id(records)
        if generated_at is None:
            generated_at = max([record.timestamp for record in records], default=0)
        if trading_date is None:
            trading_date = self._trading_date(generated_at)

        buckets = {}
        totals = _MutableDailyBucket(bucket_type="total", bucket_value="all")

        for fill in fills:
            decision = decisions.get(fill.decision_id)
            decision_record = decision_records.get(fill.decision_id)
            gross_pnl, fees, net_pnl = self._pnl(fill)
            anomaly_count = self._anomaly_count(fill)
            totals.add_fill(gross_pnl=gross_pnl, fees=fees, net_pnl=net_pnl, anomaly_count=anomaly_count)
            for bucket_type, bucket_values in self._bucket_values(fill, decision, decision_record).items():
                for bucket_value in bucket_values:
                    bucket = buckets.setdefault(
                        (bucket_type, bucket_value),
                        _MutableDailyBucket(bucket_type=bucket_type, bucket_value=bucket_value),
                    )
                    bucket.add_fill(
                        gross_pnl=gross_pnl,
                        fees=fees,
                        net_pnl=net_pnl,
                        anomaly_count=anomaly_count,
                    )

        for order in rejected_orders:
            decision = decisions.get(order.decision_id)
            decision_record = decision_records.get(order.decision_id)
            anomaly_count = self._anomaly_count(order)
            totals.add_rejection(anomaly_count=anomaly_count)
            for bucket_type, bucket_values in self._bucket_values(order, decision, decision_record).items():
                for bucket_value in bucket_values:
                    bucket = buckets.setdefault(
                        (bucket_type, bucket_value),
                        _MutableDailyBucket(bucket_type=bucket_type, bucket_value=bucket_value),
                    )
                    bucket.add_rejection(anomaly_count=anomaly_count)

        report_buckets = [
            bucket.to_schema()
            for bucket in sorted(
                buckets.values(),
                key=lambda value: (value.bucket_type, value.bucket_value),
            )
        ]
        summary_text = self._render_summary(
            trading_date=trading_date,
            run_id=run_id,
            totals=totals,
            buckets=report_buckets,
        )

        return DailyReviewReport(
            report_id=report_id,
            run_id=run_id,
            trading_date=trading_date,
            generated_at=generated_at,
            buckets=report_buckets,
            total_gross_pnl=totals.gross_pnl,
            total_fees=totals.fees,
            total_net_pnl=totals.net_pnl,
            fill_count=totals.fill_count,
            winning_trades=totals.winning_trades,
            losing_trades=totals.losing_trades,
            rejection_count=totals.rejection_count,
            anomaly_count=totals.anomaly_count,
            summary_text=summary_text,
        )

    def _infer_run_id(self, records):
        for record in records:
            run_id = getattr(record, "run_id", None)
            if run_id is not None:
                return run_id
        return "unknown"

    def _trading_date(self, timestamp):
        if timestamp <= 0:
            return "unknown"
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()

    def _pnl(self, fill):
        net_pnl = float(fill.metadata.get("realized_pnl", 0.0))
        fees = float(fill.metadata.get("fee", fill.commission))
        gross_pnl = net_pnl + fees
        return gross_pnl, fees, net_pnl

    def _anomaly_count(self, record):
        if record.metadata.get("anomaly") is True:
            return 1
        anomalies = record.metadata.get("anomalies")
        if isinstance(anomalies, list):
            return len(anomalies)
        if anomalies:
            return 1
        if record.metadata.get("error") or record.metadata.get("exception"):
            return 1
        return 0

    def _bucket_values(self, record, decision, decision_record=None):
        strategy_id = record.metadata.get("strategy_id")
        regime = record.metadata.get("regime")
        reason_codes = record.metadata.get("reason_codes")

        if decision is not None:
            strategy_id = strategy_id or decision.strategy_id
            regime = regime or decision.regime
            reason_codes = reason_codes or decision.reason_codes

        if isinstance(reason_codes, str):
            reason_codes = [reason_codes]

        feature_buckets = self._feature_bucket_values(record, decision_record)

        return {
            "symbol": [record.symbol],
            "strategy": [strategy_id or "unknown"],
            "regime": [regime or "unknown"],
            "reason": list(reason_codes or ["unknown"]),
            "feature": feature_buckets or ["unknown"],
        }

    def _feature_bucket_values(self, record, decision_record=None):
        snapshot = (
            record.metadata.get("feature_snapshot")
            or record.metadata.get("features")
            or {}
        )
        if not snapshot and decision_record is not None:
            snapshot = (
                getattr(decision_record, "feature_snapshot", None)
                or decision_record.metadata.get("feature_snapshot")
                or decision_record.metadata.get("features")
                or {}
            )
        if hasattr(snapshot, "to_payload"):
            snapshot = snapshot.to_payload()
        values = snapshot.get("values") if isinstance(snapshot, dict) else None
        if values is None and isinstance(snapshot, dict):
            values = snapshot
        if not isinstance(values, dict):
            return []

        buckets = []
        for feature_name, feature_value in sorted(values.items()):
            bucket = self._feature_value_bucket(feature_value)
            if bucket is not None:
                buckets.append(f"{feature_name}:{bucket}")
        return buckets

    def _feature_value_bucket(self, value):
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            if value > 0.0:
                return "positive"
            if value < 0.0:
                return "negative"
            return "zero"
        if isinstance(value, str) and value:
            return value
        return None

    def _render_summary(self, trading_date, run_id, totals, buckets):
        lines = [
            f"# 每日交易复盘 {trading_date}",
            "",
            f"- run_id: {run_id}",
            f"- 总收益: {totals.net_pnl:.4f}（毛收益 {totals.gross_pnl:.4f}，费用 {totals.fees:.4f}）",
            f"- 成交: {totals.fill_count}，盈利: {totals.winning_trades}，亏损: {totals.losing_trades}",
            f"- 拒绝: {totals.rejection_count}，异常: {totals.anomaly_count}",
        ]

        for bucket_type, title in [
            ("symbol", "按标的"),
            ("strategy", "按策略"),
            ("regime", "按市场状态"),
            ("reason", "按原因码"),
            ("feature", "按特征分桶"),
        ]:
            lines.extend(["", f"## {title}"])
            matching = [bucket for bucket in buckets if bucket.bucket_type == bucket_type]
            if not matching:
                lines.append("- 无记录")
                continue
            for bucket in matching:
                lines.append(
                    "- "
                    f"{bucket.bucket_value}: "
                    f"净收益 {bucket.net_pnl:.4f}, "
                    f"成交 {bucket.fill_count}, "
                    f"胜率 {bucket.win_rate:.2%}, "
                    f"Sharpe {bucket.sharpe:.4f}, "
                    f"最大回撤 {bucket.max_drawdown:.4f}, "
                    f"盈利 {bucket.winning_trades}, "
                    f"亏损 {bucket.losing_trades}, "
                    f"拒绝 {bucket.rejection_count}, "
                    f"异常 {bucket.anomaly_count}"
                )
        return "\n".join(lines)


class _MutableDailyBucket:
    def __init__(self, bucket_type, bucket_value):
        self.bucket_type = bucket_type
        self.bucket_value = bucket_value
        self.gross_pnl = 0.0
        self.fees = 0.0
        self.net_pnl = 0.0
        self.fill_count = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.rejection_count = 0
        self.anomaly_count = 0
        self._net_pnl_series = []

    def add_fill(self, gross_pnl, fees, net_pnl, anomaly_count):
        self.gross_pnl += gross_pnl
        self.fees += fees
        self.net_pnl += net_pnl
        self._net_pnl_series.append(net_pnl)
        self.fill_count += 1
        if net_pnl > 0.0:
            self.winning_trades += 1
        elif net_pnl < 0.0:
            self.losing_trades += 1
        self.anomaly_count += anomaly_count

    def add_rejection(self, anomaly_count):
        self.rejection_count += 1
        self.anomaly_count += anomaly_count

    def to_schema(self):
        average_net_pnl = self.net_pnl / self.fill_count if self.fill_count else 0.0
        return DailyReviewBucket(
            bucket_type=self.bucket_type,
            bucket_value=self.bucket_value,
            gross_pnl=self.gross_pnl,
            fees=self.fees,
            net_pnl=self.net_pnl,
            average_net_pnl=average_net_pnl,
            win_rate=self.winning_trades / self.fill_count if self.fill_count else 0.0,
            sharpe=self._sharpe(),
            max_drawdown=self._max_drawdown(),
            fill_count=self.fill_count,
            winning_trades=self.winning_trades,
            losing_trades=self.losing_trades,
            rejection_count=self.rejection_count,
            anomaly_count=self.anomaly_count,
        )

    def _sharpe(self):
        if len(self._net_pnl_series) < 2:
            return 0.0
        mean = sum(self._net_pnl_series) / len(self._net_pnl_series)
        variance = sum((value - mean) ** 2 for value in self._net_pnl_series) / len(self._net_pnl_series)
        if variance <= 0.0:
            return 0.0
        return mean / sqrt(variance)

    def _max_drawdown(self):
        peak = 0.0
        equity = 0.0
        max_drawdown = 0.0
        for net_pnl in self._net_pnl_series:
            equity += net_pnl
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
        return max_drawdown
