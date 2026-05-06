import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.exchange.okx import OKXAdapter  # noqa: E402
from quant.data.multi_timeframe import (  # noqa: E402
    MultiTimeframeDataRequest,
    MultiTimeframeKlineProvider,
)
from quant.data.quality import validate_multi_timeframe_klines  # noqa: E402
from quant.features.multi_timeframe import (  # noqa: E402
    MultiTimeframeFeaturePipeline,
    MultiTimeframeFeaturePipelineInput,
)
from quant.features.pipeline import FeaturePipelineConfig  # noqa: E402


DEFAULT_CONTEXT_TIMEFRAMES = ["15m", "1h", "4h"]
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "logs" / "real-data-feature" / "latest.json"
DEFAULT_ACCOUNT_EQUITY = 10000.0


def _payload(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "to_display_payload"):
        return value.to_display_payload()
    if hasattr(value, "to_payload"):
        return value.to_payload()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    return value


def _timeframe_data_summary(batch) -> Dict[str, Dict[str, Any]]:
    summary: Dict[str, Dict[str, Any]] = {}
    for timeframe, timeframe_batch in batch.timeframe_batches.items():
        last = timeframe_batch.klines[-1] if timeframe_batch.klines else None
        last_complete = next(
            (kline for kline in reversed(timeframe_batch.klines) if getattr(kline, "is_complete", None) is not False),
            None,
        )
        summary[timeframe] = {
            "role": timeframe_batch.role,
            "venue": timeframe_batch.venue,
            "bar_count": len(timeframe_batch.klines),
            "raw_first_timestamp": timeframe_batch.first_timestamp,
            "raw_last_timestamp": timeframe_batch.last_timestamp,
            "raw_last_close": None if last is None else last.close,
            "raw_last_is_complete": None if last is None else last.is_complete,
            "last_complete_timestamp": None if last_complete is None else last_complete.timestamp,
            "last_complete_close": None if last_complete is None else last_complete.close,
        }
    return summary


def _quality_report_payload(quality_report) -> Dict[str, Any]:
    payload = _payload(quality_report)
    for report in payload.get("timeframe_reports", {}).values():
        raw_last_timestamp = report.get("last_timestamp")
        report["raw_last_timestamp"] = raw_last_timestamp
        if report.get("has_incomplete_last_bar"):
            interval = report.get("interval_seconds")
            report["last_complete_timestamp"] = (
                raw_last_timestamp - interval
                if raw_last_timestamp is not None and interval is not None
                else None
            )
        else:
            report["last_complete_timestamp"] = raw_last_timestamp
    return payload


def _feature_payload(feature_snapshot) -> Dict[str, Any]:
    payload = _payload(feature_snapshot)
    for ref in payload.get("quality_report_refs", {}).values():
        raw_last_timestamp = ref.get("last_timestamp")
        ref["raw_last_timestamp"] = raw_last_timestamp
        # The FeatureSnapshot effective timestamp is stored per computed timeframe.
        snapshot = payload.get("timeframe_snapshots", {}).get(ref.get("timeframe"), {})
        ref["last_complete_timestamp"] = snapshot.get("timestamp")
    return payload


def _feature_summary(feature_snapshot) -> Dict[str, Any]:
    rows: Dict[str, Any] = {}
    for timeframe, snapshot in feature_snapshot.timeframe_snapshots.items():
        rows[timeframe] = {
            "feature_timestamp": snapshot.timestamp,
            "effective_bar_timestamp": snapshot.timestamp,
            "input_bar_count": snapshot.input_bar_count,
            "effective_index": snapshot.effective_index,
            "is_complete_bar": snapshot.is_complete_bar,
            "skipped_incomplete_last_bar": snapshot.skipped_incomplete_last_bar,
            "close": snapshot.values.get("close"),
            "ma_fast": snapshot.values.get("ma_fast"),
            "ma_slow": snapshot.values.get("ma_slow"),
            "rsi": snapshot.values.get("rsi"),
            "atr": snapshot.values.get("atr"),
            "macd": snapshot.values.get("macd"),
            "market_structure.structure_state": snapshot.values.get("market_structure.structure_state"),
            "market_structure.breakout_direction": snapshot.values.get("market_structure.breakout_direction"),
        }
    return {
        "snapshot_id": feature_snapshot.snapshot_id,
        "symbol": feature_snapshot.symbol,
        "execution_timeframe": feature_snapshot.execution_timeframe,
        "feature_timestamp": feature_snapshot.timestamp,
        "timeframes": rows,
        "alignment_features": feature_snapshot.alignment_features,
    }


def _bias_from_snapshot(snapshot_payload: Dict[str, Any]) -> str:
    fast = snapshot_payload.get("ma_fast")
    slow = snapshot_payload.get("ma_slow")
    if fast is None or slow is None:
        return "unknown"
    if fast > slow:
        return "bullish"
    if fast < slow:
        return "bearish"
    return "neutral"


def _build_regime_layer(feature_snapshot) -> Dict[str, Any]:
    summary = _feature_summary(feature_snapshot)
    alignment = summary["alignment_features"]
    execution_tf = summary["execution_timeframe"]
    timeframes = summary["timeframes"]
    execution = timeframes[execution_tf]
    context_timeframes = [tf for tf in timeframes if tf != execution_tf]
    context_biases = {tf: _bias_from_snapshot(timeframes[tf]) for tf in context_timeframes}
    execution_bias = alignment.get("execution_bias") or _bias_from_snapshot(execution)
    conflict_timeframes = [
        tf for tf in context_timeframes
        if alignment.get(f"execution_aligned_with_{tf}") is False
    ]
    confirmation_timeframes = [
        tf for tf in context_timeframes
        if alignment.get(f"execution_aligned_with_{tf}") is True
    ]
    rsi = execution.get("rsi")
    atr = execution.get("atr")
    structure_state = execution.get("market_structure.structure_state")
    breakout_direction = execution.get("market_structure.breakout_direction")
    if structure_state == "range" and execution_bias in {"bullish", "bearish"}:
        regime = "weak_trend" if breakout_direction not in {"up", "down"} else "trend"
    elif structure_state in {"trend", "breakout"}:
        regime = "trend"
    elif structure_state:
        regime = structure_state
    else:
        regime = "unknown"
    volatility_state = "unknown"
    if atr is not None and execution.get("close"):
        atr_ratio = atr / execution["close"]
        if atr_ratio >= 0.03:
            volatility_state = "extreme"
        elif atr_ratio >= 0.015:
            volatility_state = "high"
        else:
            volatility_state = "normal"
    tradability = "tradable"
    reason_codes = []
    if conflict_timeframes:
        tradability = "observe_only"
        reason_codes.append("higher_timeframe_conflict")
    if alignment.get("higher_timeframe_bias") == "mixed":
        tradability = "observe_only"
        reason_codes.append("higher_timeframe_bias_mixed")
    if rsi is not None and (rsi >= 80 or rsi <= 20):
        tradability = "observe_only"
        reason_codes.append("execution_rsi_extreme")
    if volatility_state == "extreme":
        tradability = "avoid"
        reason_codes.append("execution_volatility_extreme")
    if not reason_codes:
        reason_codes.append("regime_context_ok")
    return {
        "snapshot_id": f"real-mtf-regime:{summary['snapshot_id']}",
        "timestamp": summary["feature_timestamp"],
        "symbol": summary["symbol"],
        "execution_timeframe": execution_tf,
        "aggregate_regime": {
            "regime": regime,
            "direction": execution_bias,
            "structure_state": structure_state,
            "breakout_direction": breakout_direction,
            "volatility_state": volatility_state,
            "tradability": tradability,
            "confidence": 0.72 if tradability == "tradable" else 0.55,
            "reason_codes": reason_codes,
        },
        "execution_regime": {
            "timeframe": execution_tf,
            "direction": execution_bias,
            "rsi": rsi,
            "atr": atr,
            "structure_state": structure_state,
        },
        "context_regimes": {
            tf: {"timeframe": tf, "direction": context_biases[tf], "structure_state": timeframes[tf].get("market_structure.structure_state")}
            for tf in context_timeframes
        },
        "higher_timeframe_bias": alignment.get("higher_timeframe_bias"),
        "confirmation_timeframes": confirmation_timeframes,
        "conflict_timeframes": conflict_timeframes,
        "input_refs": {"feature_snapshot_id": summary["snapshot_id"]},
    }


def _build_strategy_route_layer(regime_layer: Dict[str, Any]) -> Dict[str, Any]:
    aggregate = regime_layer["aggregate_regime"]
    route_mode = "observe_only" if aggregate["tradability"] == "observe_only" else "trade" if aggregate["tradability"] == "tradable" else "blocked"
    route_name = "observe_only" if route_mode == "observe_only" else "ma_crossover_trend" if aggregate["regime"] in {"trend", "weak_trend"} else "observe_range"
    return {
        "route_id": f"real-route:{regime_layer['symbol']}:{regime_layer['execution_timeframe']}:{route_name}",
        "symbol": regime_layer["symbol"],
        "timeframe": regime_layer["execution_timeframe"],
        "selected_strategy": route_name,
        "route_mode": route_mode,
        "strategy_id": "ma_crossover",
        "strategy_version": "1.0",
        "reason_codes": [f"regime:{aggregate['regime']}", f"tradability:{aggregate['tradability']}"],
        "input_refs": {"regime_snapshot_id": regime_layer["snapshot_id"]},
    }


def _build_strategy_signal_layer(feature_snapshot, regime_layer: Dict[str, Any], route_layer: Dict[str, Any]) -> Dict[str, Any]:
    feature_summary = _feature_summary(feature_snapshot)
    execution_tf = feature_summary["execution_timeframe"]
    execution = feature_summary["timeframes"][execution_tf]
    bias = regime_layer["aggregate_regime"]["direction"]
    raw_action = "buy" if bias == "bullish" else "sell" if bias == "bearish" else "hold"
    trade_now = raw_action in {"buy", "sell"}
    reason_codes = ["ma_fast_above_ma_slow" if raw_action == "buy" else "ma_fast_below_ma_slow" if raw_action == "sell" else "ma_no_direction"]
    filtered_action = raw_action
    should_send_order = trade_now
    if regime_layer["aggregate_regime"]["tradability"] != "tradable":
        filtered_action = "wait" if regime_layer["aggregate_regime"]["tradability"] == "observe_only" else "no_trade"
        should_send_order = False
        reason_codes.extend(regime_layer["aggregate_regime"]["reason_codes"])
    return {
        "signal_id": f"real-signal:{feature_summary['snapshot_id']}",
        "strategy_id": route_layer["strategy_id"],
        "strategy_version": route_layer["strategy_version"],
        "symbol": feature_summary["symbol"],
        "timeframe": execution_tf,
        "timestamp": feature_summary["feature_timestamp"],
        "raw_signal": {
            "action": raw_action,
            "side": raw_action if raw_action in {"buy", "sell"} else None,
            "reference_price": execution.get("close"),
        },
        "filtered_signal": {
            "action": filtered_action,
            "side": raw_action if raw_action in {"buy", "sell"} else None,
            "trade_now": filtered_action in {"buy", "sell"},
            "should_send_order": should_send_order,
            "confidence": regime_layer["aggregate_regime"]["confidence"],
            "reason_codes": reason_codes,
            "watch_plan": {
                "higher_timeframe_bias": regime_layer["higher_timeframe_bias"],
                "confirmation_timeframes": regime_layer["confirmation_timeframes"],
                "conflict_timeframes": regime_layer["conflict_timeframes"],
                "tradability": regime_layer["aggregate_regime"]["tradability"],
            },
        },
        "input_refs": {
            "route_id": route_layer["route_id"],
            "feature_snapshot_id": feature_summary["snapshot_id"],
            "regime_snapshot_id": regime_layer["snapshot_id"],
        },
    }


def _build_decision_layer(
    signal_layer: Dict[str, Any],
    regime_layer: Dict[str, Any],
    *,
    force_forward_to_capital: bool = False,
) -> Dict[str, Any]:
    signal = signal_layer["filtered_signal"]
    if signal["should_send_order"] or force_forward_to_capital:
        action = "APPROVE_TRADE_INTENT"
        forward = True
        reason_codes = list(signal["reason_codes"]) + ["decision_policy_approved"]
        if force_forward_to_capital and not signal["should_send_order"]:
            reason_codes.append("forced_forward_to_capital_for_layer_validation")
        trade_intent = {
            "trade_intent_id": f"{signal_layer['signal_id']}:trade-intent",
            "symbol": signal_layer["symbol"],
            "side": signal["side"],
            "timeframe": signal_layer["timeframe"],
            "entry_price": signal_layer["raw_signal"]["reference_price"],
            "confidence": signal["confidence"],
        }
    else:
        action = "WATCH"
        forward = False
        reason_codes = [f"strategy_action_{signal['action']}"] + list(signal["reason_codes"])
        trade_intent = None
    return {
        "result_id": f"{signal_layer['signal_id']}:decision-result",
        "timestamp": signal_layer["timestamp"],
        "symbol": signal_layer["symbol"],
        "decision_action": action,
        "forward_to_capital_allocation": forward,
        "reason_codes": reason_codes,
        "trade_intent": trade_intent,
        "policy_snapshot": {
            "require_orderable_signal": True,
            "enforce_regime_alignment": True,
            "live_orders_allowed": False,
        },
        "input_refs": {
            "signal_id": signal_layer["signal_id"],
            "regime_snapshot_id": regime_layer["snapshot_id"],
        },
    }


def _build_capital_layer(decision_layer: Dict[str, Any], account_equity: float) -> Dict[str, Any]:
    trade_intent = decision_layer.get("trade_intent")
    if not decision_layer["forward_to_capital_allocation"] or trade_intent is None:
        return {
            "status": "skipped",
            "approved": False,
            "reason_codes": ["decision_not_forwarded_to_capital_allocation"],
            "input_refs": {"decision_result_id": decision_layer["result_id"]},
        }
    price = trade_intent["entry_price"]
    max_notional = account_equity * 0.10
    risk_budget = account_equity * 0.01
    quantity = max_notional / price if price else 0.0
    return {
        "status": "approved",
        "approved": True,
        "account_equity": account_equity,
        "max_notional": max_notional,
        "risk_budget": risk_budget,
        "allocated_notional": max_notional,
        "allocated_quantity": quantity,
        "reason_codes": ["capital_budget_approved_read_only"],
        "input_refs": {
            "decision_result_id": decision_layer["result_id"],
            "trade_intent_id": trade_intent["trade_intent_id"],
        },
    }


def _build_risk_layer(decision_layer: Dict[str, Any], capital_layer: Dict[str, Any]) -> Dict[str, Any]:
    trade_intent = decision_layer.get("trade_intent")
    if not capital_layer.get("approved") or trade_intent is None:
        return {
            "status": "skipped",
            "approved": False,
            "reason_codes": ["no_capital_budget_from_decision"],
            "input_refs": {"capital_status": capital_layer["status"]},
        }
    allocated_notional = capital_layer["allocated_notional"]
    account_equity = capital_layer["account_equity"]
    max_notional_limit = account_equity * 0.20
    approved = allocated_notional <= max_notional_limit and capital_layer["allocated_quantity"] > 0
    return {
        "status": "approved" if approved else "rejected",
        "approved": approved,
        "checks": {
            "max_notional_limit": max_notional_limit,
            "allocated_notional": allocated_notional,
            "quantity_positive": capital_layer["allocated_quantity"] > 0,
            "kill_switch_active": False,
        },
        "reason_codes": ["risk_approved_read_only"] if approved else ["risk_limit_rejected"],
        "order_intent": None if not approved else {
            "order_intent_id": f"{trade_intent['trade_intent_id']}:order-intent",
            "symbol": trade_intent["symbol"],
            "side": trade_intent["side"],
            "order_type": "market",
            "quantity": capital_layer["allocated_quantity"],
            "reference_price": trade_intent["entry_price"],
            "time_in_force": "IOC",
        },
        "input_refs": {
            "trade_intent_id": trade_intent["trade_intent_id"],
            "capital_status": capital_layer["status"],
        },
    }


def _build_execution_layer(risk_layer: Dict[str, Any]) -> Dict[str, Any]:
    order_intent = risk_layer.get("order_intent")
    if not risk_layer.get("approved") or order_intent is None:
        return {
            "status": "skipped",
            "broker_called": False,
            "live_orders_sent": False,
            "reason_codes": ["no_risk_approved_order"],
        }
    return {
        "status": "dry_run_ready",
        "broker_called": False,
        "live_orders_sent": False,
        "simulated_execution_result": {
            "order_id": f"dry-run:{order_intent['order_intent_id']}",
            "symbol": order_intent["symbol"],
            "side": order_intent["side"],
            "status": "not_sent_read_only",
            "quantity": order_intent["quantity"],
            "reference_price": order_intent["reference_price"],
        },
        "safety_flags": {
            "public_market_data_only": True,
            "credentials_required": False,
            "broker_called": False,
            "live_orders_sent": False,
        },
        "input_refs": {"order_intent_id": order_intent["order_intent_id"]},
    }


def _build_downstream_layers(
    feature_snapshot,
    account_equity: float,
    *,
    force_forward_to_capital: bool = False,
) -> Dict[str, Any]:
    regime = _build_regime_layer(feature_snapshot)
    route = _build_strategy_route_layer(regime)
    signal = _build_strategy_signal_layer(feature_snapshot, regime, route)
    decision = _build_decision_layer(signal, regime, force_forward_to_capital=force_forward_to_capital)
    capital = _build_capital_layer(decision, account_equity)
    risk = _build_risk_layer(decision, capital)
    execution = _build_execution_layer(risk)
    return {
        "regime_layer": regime,
        "strategy_route_layer": route,
        "strategy_signal_layer": signal,
        "decision_layer": decision,
        "capital_layer": capital,
        "risk_layer": risk,
        "execution_layer": execution,
    }


def run_real_data_feature(
    *,
    symbol: str,
    execution_timeframe: str,
    context_timeframes: list,
    limit: int,
    account_equity: float = DEFAULT_ACCOUNT_EQUITY,
    output_path: Optional[Path] = None,
    force_forward_to_capital: bool = False,
) -> Dict[str, Any]:
    adapter = OKXAdapter(require_credentials=False)
    normalized_symbol = adapter._normalize_symbol(symbol)
    provider = MultiTimeframeKlineProvider(adapter, venue="okx")
    data_request = MultiTimeframeDataRequest(
        symbol=normalized_symbol,
        venue="okx",
        execution_timeframe=execution_timeframe,
        context_timeframes=context_timeframes,
        limit=limit,
    )

    data_batch = provider.get_multi_timeframe_klines(data_request)
    quality_report = validate_multi_timeframe_klines(data_batch)
    feature_snapshot = MultiTimeframeFeaturePipeline(
        FeaturePipelineConfig(include_incomplete_last_bar=False)
    ).compute(
        MultiTimeframeFeaturePipelineInput(
            batch=data_batch,
            quality_report=quality_report,
            snapshot_id=f"real-okx:{normalized_symbol}:{execution_timeframe}:{int(datetime.now(timezone.utc).timestamp())}",
        )
    )
    downstream_layers = _build_downstream_layers(
        feature_snapshot,
        account_equity,
        force_forward_to_capital=force_forward_to_capital,
    )

    artifact = {
        "run": {
            "mode": "read_only_public_market_data_to_dry_run_execution_plan",
            "provider": "okx_public_rest",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "live_orders_sent": False,
            "credentials_required": False,
            "broker_called": False,
        },
        "request": _payload(data_request),
        "data_layer": {
            "input_type": "MultiTimeframeKlineBatch",
            "summary": _timeframe_data_summary(data_batch),
            "payload": _payload(data_batch),
        },
        "data_quality": _quality_report_payload(quality_report),
        "feature_layer": {
            "output_type": "MultiTimeframeFeatureSnapshot",
            "summary": _feature_summary(feature_snapshot),
            "payload": _feature_payload(feature_snapshot),
        },
        **downstream_layers,
    }

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    return artifact


def _print_layer_sections(artifact: Dict[str, Any], *, include_data: bool = False, full: bool = False) -> None:
    sections = []
    if include_data:
        sections.extend([
            ("DATA 层输出", artifact["data_layer"] if full else artifact["data_layer"]["summary"]),
            ("DATA QUALITY 层输出", artifact["data_quality"]),
        ])
    sections.extend([
        ("Feature 层输出", artifact["feature_layer"] if full else artifact["feature_layer"]["summary"]),
        ("Regime 层输出", artifact["regime_layer"]),
        ("策略路由 层输出", artifact["strategy_route_layer"]),
        ("策略信号 层输出", artifact["strategy_signal_layer"]),
        ("决策 层输出", artifact["decision_layer"]),
        ("资金分配 层输出", artifact["capital_layer"]),
        ("风险 层输出", artifact["risk_layer"]),
        ("执行 层输出", artifact["execution_layer"]),
    ])
    for title, payload in sections:
        print(f"--{title}--")
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch real OKX public multi-timeframe DATA input and print Feature→Execution layer outputs."
    )
    parser.add_argument("--symbol", default="BTCUSDT", help="Market symbol, e.g. BTCUSDT or BTC-USDT")
    parser.add_argument("--execution-timeframe", default="5m", choices=["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"])
    parser.add_argument(
        "--context-timeframes",
        default=",".join(DEFAULT_CONTEXT_TIMEFRAMES),
        help="Comma-separated higher/context timeframes, e.g. 15m,1h,4h",
    )
    parser.add_argument("--limit", type=int, default=120, help="Bars per timeframe")
    parser.add_argument("--account-equity", type=float, default=DEFAULT_ACCOUNT_EQUITY, help="Dry-run account equity for capital/risk sizing")
    parser.add_argument(
        "--force-forward-to-capital",
        action="store_true",
        help="Force decision forward for validating capital/risk/execution dry-run layers even when signal is wait/no_trade",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="JSON artifact output path")
    parser.add_argument("--print-full", action="store_true", help="For Feature/DATA sections, print full payload instead of summary")
    parser.add_argument("--include-data", action="store_true", help="Also print DATA and DATA QUALITY sections")
    args = parser.parse_args()

    context_timeframes = [item.strip() for item in args.context_timeframes.split(",") if item.strip()]
    artifact = run_real_data_feature(
        symbol=args.symbol,
        execution_timeframe=args.execution_timeframe,
        context_timeframes=context_timeframes,
        limit=args.limit,
        account_equity=args.account_equity,
        output_path=args.output,
        force_forward_to_capital=args.force_forward_to_capital,
    )

    print(f"artifact_path={args.output}")
    _print_layer_sections(artifact, include_data=args.include_data, full=args.print_full)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
