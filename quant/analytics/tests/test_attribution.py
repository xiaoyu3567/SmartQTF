import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import ValidationError

from quant.analytics import TradeAttributionAnalyzer
from quant.schemas import (
    AssetClass,
    AttributionBucket,
    DecisionAction,
    DecisionIntent,
    DecisionLogRecord,
    FillLogRecord,
    OrderKind,
    PayloadSource,
    TimeInForce,
    TraceContext,
    TradeSide,
)


def make_trace(run_id="bt-attr-001", symbol="BTCUSDT"):
    return TraceContext(
        run_id=run_id,
        source=PayloadSource.BACKTEST,
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


def make_fill(fill_id, decision_id, symbol, net_pnl, fee):
    return FillLogRecord(
        event_id=f"event-{fill_id}",
        run_id="bt-attr-001",
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
        metadata={"realized_pnl": net_pnl},
    )


def bucket(report, bucket_type, bucket_value):
    matches = [
        item
        for item in report.buckets
        if item.bucket_type == bucket_type and item.bucket_value == bucket_value
    ]
    assert len(matches) == 1
    return matches[0]


def test_trade_attribution_groups_pnl_by_strategy_regime_symbol_and_rule():
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
    records = [
        make_decision_record(trend_decision),
        make_decision_record(range_decision),
        make_fill("fill-001", "decision-001", "BTCUSDT", net_pnl=12.5, fee=0.5),
        make_fill("fill-002", "decision-002", "ETHUSDT", net_pnl=-3.0, fee=0.2),
    ]

    report = TradeAttributionAnalyzer().build_report(records, report_id="attr-001")

    assert report.run_id == "bt-attr-001"
    assert report.total_net_pnl == 9.5
    assert report.total_fees == 0.7
    assert report.total_gross_pnl == 10.2
    assert report.fill_count == 2
    assert report.trade_count == 2

    assert bucket(report, "strategy", "ma_crossover").net_pnl == 12.5
    assert bucket(report, "strategy", "mean_reversion").net_pnl == -3.0
    assert bucket(report, "regime", "trend").winning_trades == 1
    assert bucket(report, "regime", "range").losing_trades == 1
    assert bucket(report, "symbol", "BTCUSDT").gross_pnl == 13.0
    assert bucket(report, "rule", "ma_cross").net_pnl == 12.5
    assert bucket(report, "rule", "risk_passed").net_pnl == 12.5
    assert bucket(report, "rule", "zscore_revert").net_pnl == -3.0
    assert bucket(report, "decision_reason", "zscore_revert").losing_trades == 1


def test_trade_attribution_groups_by_feature_bucket_from_decision_snapshot():
    decision = make_decision(
        "decision-001",
        strategy_id="orderflow_breakout",
        regime="trend",
        reason_codes=["ofi_positive"],
    )
    records = [
        make_decision_record_with_features(
            decision,
            {"orderflow_imbalance": 0.8, "funding_rate": -0.0002},
        ),
        make_fill("fill-001", "decision-001", "BTCUSDT", net_pnl=6.0, fee=0.1),
    ]

    report = TradeAttributionAnalyzer().build_report(records, report_id="attr-003")

    assert bucket(report, "feature", "orderflow_imbalance:positive").net_pnl == 6.0
    assert bucket(report, "feature", "funding_rate:negative").winning_trades == 1


def test_trade_attribution_falls_back_to_fill_metadata():
    fill = make_fill("fill-001", None, "BTCUSDT", net_pnl=4.0, fee=0.1)
    fill.metadata.update(
        {
            "strategy_id": "metadata_strategy",
            "regime": "volatile",
            "reason_codes": "manual_review",
        }
    )

    report = TradeAttributionAnalyzer().build_report([fill], report_id="attr-002")

    assert bucket(report, "strategy", "metadata_strategy").net_pnl == 4.0
    assert bucket(report, "regime", "volatile").net_pnl == 4.0
    assert bucket(report, "rule", "manual_review").net_pnl == 4.0


def test_attribution_bucket_rejects_negative_counts():
    try:
        AttributionBucket(bucket_type="strategy", bucket_value="ma", fill_count=-1)
    except ValidationError:
        pass
    else:
        raise AssertionError("attribution bucket counts must be non-negative")
