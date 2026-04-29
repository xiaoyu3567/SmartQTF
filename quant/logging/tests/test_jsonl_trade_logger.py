import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import ValidationError

from quant.logging import JsonlTradeLogger
from quant.schemas import (
    AssetClass,
    DecisionAction,
    DecisionIntent,
    DecisionLogRecord,
    FeatureSnapshot,
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


def make_trace():
    return TraceContext(
        run_id="bt-log-001",
        source=PayloadSource.BACKTEST,
        symbol="BTCUSDT",
        timeframe="1m",
        timestamp=1710000000,
        bar_index=42,
    )


def make_decision():
    return DecisionIntent(
        decision_id="decision-001",
        timestamp=1710000000,
        symbol="BTCUSDT",
        asset_class=AssetClass.CRYPTO,
        strategy_id="ma_crossover",
        strategy_version="1.0.0",
        action=DecisionAction.OPEN_LONG,
        order_type=OrderKind.LIMIT,
        quantity=0.25,
        limit_price=65000.0,
        time_in_force=TimeInForce.GTC,
        reason_codes=["ma_cross_up"],
        trace=make_trace(),
    )


def make_feature_snapshot():
    return FeatureSnapshot(
        snapshot_id="features-001",
        timestamp=1710000000,
        symbol="BTCUSDT",
        timeframe="1m",
        as_of_timestamp=1710000000,
        feature_set_id="default_ma",
        feature_set_version="1.0",
        values={"fast_ma": 65100.0, "slow_ma": 64900.0},
        source_window_start=1709999940,
        source_window_end=1710000000,
        trace=make_trace(),
    )


def test_decision_order_fill_logs_round_trip(tmp_path):
    log_path = tmp_path / "trade-log.jsonl"
    logger = JsonlTradeLogger(log_path)
    trace = make_trace()

    decision_record = DecisionLogRecord(
        event_id="event-decision-001",
        run_id=trace.run_id,
        timestamp=1710000000,
        trace=trace,
        decision=make_decision(),
        feature_snapshot=make_feature_snapshot(),
    )
    order_record = OrderLogRecord(
        event_id="event-order-001",
        run_id=trace.run_id,
        timestamp=1710000001,
        trace=trace,
        order_id="order-001",
        client_order_id="client-001",
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        status=OrderStatus.ACCEPTED,
        quantity=0.25,
        remaining_quantity=0.25,
        price=65000.0,
        decision_id="decision-001",
    )
    fill_record = FillLogRecord(
        event_id="event-fill-001",
        run_id=trace.run_id,
        timestamp=1710000002,
        trace=trace,
        fill_id="fill-001",
        order_id="order-001",
        client_order_id="client-001",
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        filled_quantity=0.25,
        fill_price=65010.0,
        commission=0.01,
        decision_id="decision-001",
    )

    logger.append(decision_record)
    logger.append(order_record)
    logger.append(fill_record)

    restored = logger.read_all()

    assert restored == [decision_record, order_record, fill_record]
    assert restored[0].record_type == "decision"
    assert restored[0].feature_snapshot.snapshot_id == "features-001"
    assert restored[0].feature_snapshot.values["fast_ma"] == 65100.0
    assert restored[1].client_order_id == "client-001"
    assert restored[2].fill_price == 65010.0
    assert [record.event_id for record in logger.read_by_type("fill")] == ["event-fill-001"]


def test_log_schema_rejects_invalid_fill_quantity():
    try:
        FillLogRecord(
            event_id="event-fill-001",
            run_id="bt-log-001",
            timestamp=1710000002,
            fill_id="fill-001",
            order_id="order-001",
            client_order_id="client-001",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            filled_quantity=0.0,
            fill_price=65010.0,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("fill logs must reject zero filled_quantity")


def test_logger_rejects_unknown_record_type(tmp_path):
    log_path = tmp_path / "trade-log.jsonl"
    log_path.write_text('{"record_type": "unknown"}\n', encoding="utf-8")
    logger = JsonlTradeLogger(log_path)

    try:
        logger.read_all()
    except ValueError as exc:
        assert "unknown log record type" in str(exc)
    else:
        raise AssertionError("unknown record_type should fail replay")


def test_risk_decision_log_round_trip(tmp_path):
    log_path = tmp_path / "risk-log.jsonl"
    logger = JsonlTradeLogger(log_path)
    trace = make_trace()
    decision = RiskDecision.reject(
        "max_drawdown_exceeded",
        "account drawdown exceeded configured maximum",
        fatal=True,
    )
    record = RiskDecisionLogRecord(
        event_id="event-risk-001",
        run_id=trace.run_id,
        timestamp=1710000003,
        trace=trace,
        symbol="BTCUSDT",
        approved=False,
        reason_codes=["max_drawdown_exceeded"],
        risk_decision=decision,
        strategy_id="ma_crossover",
        decision_id="decision-001",
        metadata={"price": 65000.0},
    )

    logger.append(record)
    restored = logger.read_all()

    assert restored == [record]
    assert restored[0].record_type == "risk"
    assert restored[0].risk_decision.rejections[0].fatal is True
    assert [item.event_id for item in logger.read_by_type("risk")] == ["event-risk-001"]
