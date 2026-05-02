import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import ValidationError

from quant.analytics import DailyReviewReporter
from quant.schemas import (
    AssetClass,
    DailyReviewBucket,
    DecisionAction,
    DecisionIntent,
    DecisionLogRecord,
    FillLogRecord,
    OrderKind,
    OrderLogRecord,
    OrderStatus,
    PayloadSource,
    RiskDecision,
    RiskDecisionLogRecord,
    TimeInForce,
    TraceContext,
    TradeSide,
)


def make_trace(run_id="paper-review-001", symbol="BTCUSDT"):
    return TraceContext(
        run_id=run_id,
        source=PayloadSource.PAPER,
        symbol=symbol,
        timeframe="1m",
        timestamp=1710000000,
        bar_index=10,
    )


def make_decision(decision_id, strategy_id, regime, reason_codes, symbol="BTCUSDT"):
    return DecisionIntent(
        decision_id=decision_id,
        timestamp=1710000000,
        symbol=symbol,
        asset_class=AssetClass.CRYPTO,
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        regime=regime,
        action=DecisionAction.OPEN_LONG,
        order_type=OrderKind.MARKET,
        quantity=1.0,
        time_in_force=TimeInForce.GTC,
        reason_codes=reason_codes,
        trace=make_trace(symbol=symbol),
    )


def make_decision_record(decision):
    return DecisionLogRecord(
        event_id=f"event-{decision.decision_id}",
        run_id=decision.trace.run_id,
        timestamp=decision.timestamp,
        trace=decision.trace,
        decision=decision,
    )


def make_decision_record_with_features(decision, values):
    record = make_decision_record(decision)
    record.metadata["feature_snapshot"] = {
        "snapshot_id": f"features-{decision.decision_id}",
        "values": values,
    }
    return record


def make_fill(fill_id, decision_id, symbol, net_pnl, fee, metadata=None):
    payload_metadata = {"realized_pnl": net_pnl}
    payload_metadata.update(metadata or {})
    return FillLogRecord(
        event_id=f"event-{fill_id}",
        run_id="paper-review-001",
        timestamp=1710000060,
        trace=make_trace(symbol=symbol),
        fill_id=fill_id,
        order_id=f"order-{fill_id}",
        client_order_id=f"client-{fill_id}",
        symbol=symbol,
        side=TradeSide.BUY,
        filled_quantity=1.0,
        fill_price=100.0,
        commission=fee,
        decision_id=decision_id,
        metadata=payload_metadata,
    )


def make_rejected_order(order_id, decision_id, symbol, metadata=None):
    return OrderLogRecord(
        event_id=f"event-{order_id}",
        run_id="paper-review-001",
        timestamp=1710000120,
        trace=make_trace(symbol=symbol),
        order_id=order_id,
        client_order_id=f"client-{order_id}",
        symbol=symbol,
        side=TradeSide.BUY,
        status=OrderStatus.REJECTED,
        quantity=1.0,
        remaining_quantity=1.0,
        decision_id=decision_id,
        metadata=metadata or {},
    )


def make_order(order_id, decision_id, symbol, status, metadata=None):
    return OrderLogRecord(
        event_id=f"event-{order_id}",
        run_id="paper-review-001",
        timestamp=1710000120,
        trace=make_trace(symbol=symbol),
        order_id=order_id,
        client_order_id=f"client-{order_id}",
        symbol=symbol,
        side=TradeSide.BUY,
        status=status,
        quantity=1.0,
        remaining_quantity=1.0,
        decision_id=decision_id,
        metadata=metadata or {},
    )


def make_rejected_risk_record(decision, reason_codes):
    risk_decision = RiskDecision.reject(reason_codes[0], "risk rejected")
    return RiskDecisionLogRecord(
        event_id=f"event-risk-{decision.decision_id}",
        run_id=decision.trace.run_id,
        timestamp=decision.timestamp,
        trace=decision.trace,
        symbol=decision.symbol,
        approved=False,
        reason_codes=reason_codes,
        risk_decision=risk_decision,
        strategy_id=decision.strategy_id,
        decision_id=decision.decision_id,
    )


def bucket(report, bucket_type, bucket_value):
    matches = [
        item
        for item in report.buckets
        if item.bucket_type == bucket_type and item.bucket_value == bucket_value
    ]
    assert len(matches) == 1
    return matches[0]


def test_daily_review_report_summarizes_profit_loss_rejections_and_anomalies():
    trend_decision = make_decision(
        "decision-001",
        strategy_id="ma_crossover",
        regime="trend",
        reason_codes=["ma_cross", "risk_passed"],
    )
    range_decision = make_decision(
        "decision-002",
        strategy_id="mean_reversion",
        regime="range",
        reason_codes=["zscore_revert"],
        symbol="ETHUSDT",
    )
    rejected_decision = make_decision(
        "decision-003",
        strategy_id="ma_crossover",
        regime="volatile",
        reason_codes=["max_drawdown"],
    )
    records = [
        make_decision_record(trend_decision),
        make_decision_record(range_decision),
        make_decision_record(rejected_decision),
        make_fill("fill-001", "decision-001", "BTCUSDT", net_pnl=12.5, fee=0.5),
        make_fill(
            "fill-002",
            "decision-002",
            "ETHUSDT",
            net_pnl=-3.0,
            fee=0.2,
            metadata={"anomalies": ["late_fill"]},
        ),
        make_rejected_order(
            "order-003",
            "decision-003",
            "BTCUSDT",
            metadata={"error": "risk rejected"},
        ),
    ]

    report = DailyReviewReporter().build_report(
        records,
        report_id="daily-001",
        trading_date="2024-03-09",
    )

    assert report.run_id == "paper-review-001"
    assert report.trading_date == "2024-03-09"
    assert report.total_net_pnl == 9.5
    assert report.total_fees == 0.7
    assert report.total_gross_pnl == 10.2
    assert report.fill_count == 2
    assert report.winning_trades == 1
    assert report.losing_trades == 1
    assert report.rejection_count == 1
    assert report.anomaly_count == 2

    assert bucket(report, "symbol", "BTCUSDT").rejection_count == 1
    assert bucket(report, "strategy", "ma_crossover").net_pnl == 12.5
    assert bucket(report, "strategy", "ma_crossover").rejection_count == 1
    assert bucket(report, "strategy", "ma_crossover").win_rate == 1.0
    assert bucket(report, "regime", "range").losing_trades == 1
    assert bucket(report, "reason", "max_drawdown").rejection_count == 1
    assert "每日交易复盘 2024-03-09" in report.summary_text
    assert "按标的" in report.summary_text
    assert "拒绝: 1，异常: 2" in report.summary_text


def test_daily_review_falls_back_to_fill_metadata():
    fill = make_fill("fill-001", None, "BTCUSDT", net_pnl=4.0, fee=0.1)
    fill.metadata.update(
        {
            "strategy_id": "metadata_strategy",
            "regime": "volatile",
            "reason_codes": "manual_review",
        }
    )

    report = DailyReviewReporter().build_report([fill], report_id="daily-002")

    assert bucket(report, "strategy", "metadata_strategy").net_pnl == 4.0
    assert bucket(report, "regime", "volatile").net_pnl == 4.0
    assert bucket(report, "reason", "manual_review").net_pnl == 4.0


def test_daily_review_adds_feature_buckets_and_risk_statistics():
    bullish_decision = make_decision(
        "decision-001",
        strategy_id="ma_crossover",
        regime="trend",
        reason_codes=["feature_passed"],
    )
    bearish_decision = make_decision(
        "decision-002",
        strategy_id="ma_crossover",
        regime="trend",
        reason_codes=["feature_passed"],
    )
    records = [
        make_decision_record_with_features(
            bullish_decision,
            {"funding_rate": 0.0002, "cross_up": True, "spread": 1.5},
        ),
        make_decision_record_with_features(
            bearish_decision,
            {"funding_rate": -0.0001, "cross_up": False, "spread": -0.5},
        ),
        make_fill("fill-001", "decision-001", "BTCUSDT", net_pnl=8.0, fee=0.1),
        make_fill("fill-002", "decision-002", "BTCUSDT", net_pnl=-2.0, fee=0.1),
    ]

    report = DailyReviewReporter().build_report(records, report_id="daily-003")

    strategy_bucket = bucket(report, "strategy", "ma_crossover")
    assert strategy_bucket.fill_count == 2
    assert strategy_bucket.win_rate == 0.5
    assert strategy_bucket.average_net_pnl == 3.0
    assert strategy_bucket.sharpe == 0.6
    assert strategy_bucket.max_drawdown == 2.0

    assert bucket(report, "feature", "funding_rate:positive").net_pnl == 8.0
    assert bucket(report, "feature", "funding_rate:negative").net_pnl == -2.0
    assert bucket(report, "feature", "cross_up:true").winning_trades == 1
    assert bucket(report, "feature", "spread:negative").losing_trades == 1
    assert "按特征分桶" in report.summary_text


def test_daily_review_answers_strategy_regime_reason_risk_and_order_failure_questions():
    losing_decision = make_decision(
        "decision-lose-001",
        strategy_id="breakout",
        regime="volatile",
        reason_codes=["breakout_failed"],
    )
    rejected_decision = make_decision(
        "decision-risk-001",
        strategy_id="mean_reversion",
        regime="range",
        reason_codes=["zscore_revert"],
    )
    failed_order_decision = make_decision(
        "decision-order-001",
        strategy_id="breakout",
        regime="trend",
        reason_codes=["entry_timeout"],
        symbol="ETHUSDT",
    )
    records = [
        make_decision_record_with_features(losing_decision, {"atr_pct": 0.04}),
        make_decision_record(rejected_decision),
        make_decision_record(failed_order_decision),
        make_fill("fill-lose-001", "decision-lose-001", "BTCUSDT", net_pnl=-7.0, fee=0.1),
        make_rejected_risk_record(rejected_decision, ["risk:max_position"]),
        make_order(
            "order-timeout-001",
            "decision-order-001",
            "ETHUSDT",
            OrderStatus.UNKNOWN,
            metadata={"order_failure_reason_codes": ["broker_timeout"], "error": "timeout"},
        ),
    ]

    report = DailyReviewReporter().build_report(records, report_id="daily-004")

    assert bucket(report, "strategy", "breakout").net_pnl == -7.0
    assert bucket(report, "regime", "volatile").losing_trades == 1
    assert bucket(report, "feature", "atr_pct:positive").net_pnl == -7.0
    assert bucket(report, "decision_reason", "breakout_failed").losing_trades == 1
    assert bucket(report, "risk_rejection_reason", "risk:max_position").risk_rejection_count == 1
    assert bucket(report, "order_failure", "broker_timeout").order_failure_count == 1
    assert report.risk_rejection_count == 1
    assert report.order_failure_count == 1
    assert "按风控拒绝原因" in report.summary_text
    assert "按订单失败" in report.summary_text


def test_daily_review_bucket_rejects_negative_counts():
    try:
        DailyReviewBucket(bucket_type="symbol", bucket_value="BTCUSDT", order_failure_count=-1)
    except ValidationError:
        pass
    else:
        raise AssertionError("daily review bucket counts must be non-negative")
