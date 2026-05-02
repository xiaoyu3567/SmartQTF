import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.logging import JsonlTradeLogger, TradeJournalReconstructor
from quant.schemas.base import TraceContext
from quant.schemas.decision import DecisionIntent
from quant.schemas.enums import (
    AssetClass,
    DecisionAction,
    OrderKind,
    OrderStatus,
    PayloadSource,
    RegimeKind,
    TimeInForce,
    TradeSide,
)
from quant.schemas.feature import FeatureSnapshot
from quant.schemas.logging import (
    DecisionLogRecord,
    FillLogRecord,
    OrderLogRecord,
    PortfolioAllocationLogRecord,
    RegimeLogRecord,
    RiskDecisionLogRecord,
)
from quant.schemas.portfolio import PortfolioAllocationDecision, PortfolioAllocationItem
from quant.schemas.regime import RegimeSnapshot
from quant.schemas.risk import RiskDecision


RUN_ID = "journal-run-001"
TRADE_ID = "trade-btc-001"


def make_trace(timestamp=1710000000):
    return TraceContext(
        run_id=RUN_ID,
        source=PayloadSource.PAPER,
        symbol="BTCUSDT",
        timeframe="1m",
        timestamp=timestamp,
        bar_index=42,
    )


def make_decision(decision_id, action, quantity, limit_price, timestamp):
    return DecisionIntent(
        decision_id=decision_id,
        timestamp=timestamp,
        symbol="BTCUSDT",
        asset_class=AssetClass.CRYPTO,
        strategy_id="ma_crossover",
        strategy_version="1.0.0",
        regime="trend",
        action=action,
        order_type=OrderKind.LIMIT,
        quantity=quantity,
        limit_price=limit_price,
        time_in_force=TimeInForce.GTC,
        reduce_only=action == DecisionAction.CLOSE_LONG,
        reason_codes=["ma_cross_up" if action == DecisionAction.OPEN_LONG else "take_profit"],
        trace=make_trace(timestamp),
    )


def make_feature_snapshot():
    return FeatureSnapshot(
        snapshot_id="features-btc-001",
        timestamp=1710000000,
        symbol="BTCUSDT",
        timeframe="1m",
        as_of_timestamp=1710000000,
        feature_set_id="prod_alpha",
        feature_set_version="1.0",
        values={"trend_strength": 0.08, "atr_pct": 0.02},
        source_window_start=1709999700,
        source_window_end=1710000000,
        trace=make_trace(),
    )


def make_regime_record():
    return RegimeLogRecord(
        event_id="event-regime-001",
        run_id=RUN_ID,
        timestamp=1710000000,
        trace=make_trace(),
        metadata={"trade_id": TRADE_ID},
        regime_snapshot=RegimeSnapshot(
            regime_id="regime-btc-001",
            timestamp=1710000000,
            symbol="BTCUSDT",
            timeframe="1m",
            as_of_timestamp=1710000000,
            detector_id="adx_atr",
            detector_version="1.0",
            regime=RegimeKind.TREND,
            confidence=0.84,
            reason_codes=["adx:trend"],
            metrics={"adx": 32.0, "atr_pct": 0.02},
            trace=make_trace(),
        ),
    )


def make_portfolio_record():
    return PortfolioAllocationLogRecord(
        event_id="event-portfolio-001",
        run_id=RUN_ID,
        timestamp=1710000002,
        trace=make_trace(1710000002),
        metadata={"trade_id": TRADE_ID},
        decision_id="decision-open-001",
        allocation=PortfolioAllocationDecision(
            allocation_id="allocation-btc-001",
            timestamp=1710000002,
            approved=True,
            account_equity=100000.0,
            available_cash=90000.0,
            allocated_notional=10000.0,
            remaining_cash=80000.0,
            allocations=[
                PortfolioAllocationItem(
                    strategy_id="ma_crossover",
                    client_order_id="client-open-001",
                    symbol="BTCUSDT",
                    side=TradeSide.BUY,
                    approved=True,
                    requested_quantity=1.0,
                    allocated_quantity=1.0,
                    requested_notional=10000.0,
                    allocated_notional=10000.0,
                    reference_price=10000.0,
                    reason_codes=["portfolio:approved"],
                    trace=make_trace(1710000002),
                )
            ],
            reason_codes=["portfolio:approved"],
            trace=make_trace(1710000002),
        ),
    )


def test_trade_journal_reconstructs_entry_exit_and_pnl_from_jsonl(tmp_path):
    log_path = tmp_path / "trade-log.jsonl"
    logger = JsonlTradeLogger(log_path)
    entry_decision = make_decision(
        "decision-open-001",
        DecisionAction.OPEN_LONG,
        1.0,
        10000.0,
        1710000000,
    )
    exit_decision = make_decision(
        "decision-close-001",
        DecisionAction.CLOSE_LONG,
        1.0,
        11000.0,
        1710000600,
    )

    for record in [
        make_regime_record(),
        DecisionLogRecord(
            event_id="event-decision-open",
            run_id=RUN_ID,
            timestamp=1710000000,
            trace=make_trace(),
            metadata={"trade_id": TRADE_ID},
            decision=entry_decision,
            feature_snapshot=make_feature_snapshot(),
        ),
        RiskDecisionLogRecord(
            event_id="event-risk-open",
            run_id=RUN_ID,
            timestamp=1710000001,
            trace=make_trace(1710000001),
            metadata={"trade_id": TRADE_ID, "risk_decision_id": "risk-open-001"},
            symbol="BTCUSDT",
            approved=True,
            reason_codes=["risk:approved"],
            strategy_id="ma_crossover",
            decision_id="decision-open-001",
            risk_decision=RiskDecision.approve(
                order_payload={"client_order_id": "client-open-001"},
                reason_codes=["risk:approved"],
                risk_decision_id="risk-open-001",
            ),
        ),
        make_portfolio_record(),
        OrderLogRecord(
            event_id="event-order-open",
            run_id=RUN_ID,
            timestamp=1710000003,
            trace=make_trace(1710000003),
            metadata={
                "trade_id": TRADE_ID,
                "allocation_id": "allocation-btc-001",
                "risk_decision_id": "risk-open-001",
                "strategy_id": "ma_crossover",
            },
            order_id="order-open-001",
            client_order_id="client-open-001",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            status=OrderStatus.FILLED,
            quantity=1.0,
            filled_quantity=1.0,
            remaining_quantity=0.0,
            price=10000.0,
            decision_id="decision-open-001",
        ),
        FillLogRecord(
            event_id="event-fill-open",
            run_id=RUN_ID,
            timestamp=1710000004,
            trace=make_trace(1710000004),
            metadata={"trade_id": TRADE_ID, "trade_leg": "entry", "fee": 1.0},
            fill_id="fill-open-001",
            order_id="order-open-001",
            client_order_id="client-open-001",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            filled_quantity=1.0,
            fill_price=10010.0,
            commission=1.0,
            decision_id="decision-open-001",
        ),
        DecisionLogRecord(
            event_id="event-decision-close",
            run_id=RUN_ID,
            timestamp=1710000600,
            trace=make_trace(1710000600),
            metadata={"trade_id": TRADE_ID},
            decision=exit_decision,
        ),
        RiskDecisionLogRecord(
            event_id="event-risk-close",
            run_id=RUN_ID,
            timestamp=1710000601,
            trace=make_trace(1710000601),
            metadata={"trade_id": TRADE_ID, "risk_decision_id": "risk-close-001"},
            symbol="BTCUSDT",
            approved=True,
            reason_codes=["risk:reduce_only_exit"],
            strategy_id="ma_crossover",
            decision_id="decision-close-001",
            risk_decision=RiskDecision.approve(
                order_payload={"client_order_id": "client-close-001"},
                reason_codes=["risk:reduce_only_exit"],
                risk_decision_id="risk-close-001",
            ),
        ),
        OrderLogRecord(
            event_id="event-order-close",
            run_id=RUN_ID,
            timestamp=1710000602,
            trace=make_trace(1710000602),
            metadata={
                "trade_id": TRADE_ID,
                "allocation_id": "allocation-btc-001",
                "risk_decision_id": "risk-close-001",
            },
            order_id="order-close-001",
            client_order_id="client-close-001",
            symbol="BTCUSDT",
            side=TradeSide.SELL,
            status=OrderStatus.FILLED,
            quantity=1.0,
            filled_quantity=1.0,
            remaining_quantity=0.0,
            price=11000.0,
            decision_id="decision-close-001",
        ),
        FillLogRecord(
            event_id="event-fill-close",
            run_id=RUN_ID,
            timestamp=1710000603,
            trace=make_trace(1710000603),
            metadata={"trade_id": TRADE_ID, "trade_leg": "exit", "fee": 1.5},
            fill_id="fill-close-001",
            order_id="order-close-001",
            client_order_id="client-close-001",
            symbol="BTCUSDT",
            side=TradeSide.SELL,
            filled_quantity=1.0,
            fill_price=10990.0,
            commission=1.5,
            decision_id="decision-close-001",
        ),
    ]:
        logger.append(record)

    restored = logger.read_all()
    journal = TradeJournalReconstructor().reconstruct(restored)

    assert len(journal) == 1
    entry = journal[0]
    assert entry.status == "closed"
    assert entry.trade_id == TRADE_ID
    assert entry.symbol == "BTCUSDT"
    assert entry.strategy_id == "ma_crossover"
    assert entry.regime == "trend"
    assert entry.regime_snapshot_id == "regime-btc-001"
    assert entry.feature_snapshot_id == "features-btc-001"
    assert entry.feature_values["trend_strength"] == 0.08
    assert entry.risk_approved is True
    assert entry.risk_decision_ids == ["risk-open-001", "risk-close-001"]
    assert entry.allocation_ids == ["allocation-btc-001"]
    assert entry.decision_reason_codes == ["ma_cross_up", "take_profit"]
    assert entry.entry_side == TradeSide.BUY
    assert entry.exit_side == TradeSide.SELL
    assert entry.entry_quantity == 1.0
    assert entry.exit_quantity == 1.0
    assert entry.entry_avg_price == 10010.0
    assert entry.exit_avg_price == 10990.0
    assert entry.expected_entry_price == 10000.0
    assert entry.expected_exit_price == 11000.0
    assert entry.entry_slippage == 10.0
    assert entry.exit_slippage == 10.0
    assert entry.gross_pnl == 980.0
    assert entry.fees == 2.5
    assert entry.net_pnl == 977.5
    assert entry.realized_pnl_source == "calculated_from_fills"
    assert [item["record_type"] for item in entry.timeline] == [
        "regime",
        "decision",
        "risk",
        "portfolio",
        "order",
        "fill",
        "decision",
        "risk",
        "order",
        "fill",
    ]


def test_trade_journal_keeps_rejected_risk_reason_without_fill(tmp_path):
    logger = JsonlTradeLogger(tmp_path / "rejected-log.jsonl")
    decision = make_decision(
        "decision-reject-001",
        DecisionAction.OPEN_LONG,
        1.0,
        10000.0,
        1710000000,
    )

    for record in [
        DecisionLogRecord(
            event_id="event-decision-reject",
            run_id=RUN_ID,
            timestamp=1710000000,
            trace=make_trace(),
            metadata={"trade_id": "trade-reject-001"},
            decision=decision,
            feature_snapshot=make_feature_snapshot(),
        ),
        RiskDecisionLogRecord(
            event_id="event-risk-reject",
            run_id=RUN_ID,
            timestamp=1710000001,
            trace=make_trace(1710000001),
            metadata={"trade_id": "trade-reject-001"},
            symbol="BTCUSDT",
            approved=False,
            reason_codes=["risk:max_position"],
            strategy_id="ma_crossover",
            decision_id="decision-reject-001",
            risk_decision=RiskDecision.reject(
                "risk:max_position",
                "max position exceeded",
                risk_decision_id="risk-reject-001",
            ),
        ),
        OrderLogRecord(
            event_id="event-order-reject",
            run_id=RUN_ID,
            timestamp=1710000002,
            trace=make_trace(1710000002),
            metadata={"trade_id": "trade-reject-001", "risk_decision_id": "risk-reject-001"},
            order_id="order-reject-001",
            client_order_id="client-reject-001",
            symbol="BTCUSDT",
            side=TradeSide.BUY,
            status=OrderStatus.REJECTED,
            quantity=1.0,
            filled_quantity=0.0,
            remaining_quantity=1.0,
            price=10000.0,
            decision_id="decision-reject-001",
        ),
    ]:
        logger.append(record)

    journal = TradeJournalReconstructor().reconstruct_from_logger(logger)

    assert len(journal) == 1
    entry = journal[0]
    assert entry.trade_id == "trade-reject-001"
    assert entry.status == "rejected"
    assert entry.risk_approved is False
    assert entry.risk_reason_codes == ["risk:max_position"]
    assert entry.entry_quantity == 0.0
    assert entry.net_pnl == 0.0
