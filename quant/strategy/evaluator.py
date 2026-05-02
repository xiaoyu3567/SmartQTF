import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from quant.schemas import (
    RegimeKind,
    RegimeSnapshot,
    StrategyAction,
    StrategyCandidateScore,
    StrategyEvaluationResult,
    StrategyPerformanceFeedback,
    StrategySignal,
)
from quant.strategy.stateless import StatelessStrategyValidator
from quant.strategy.router import RoutedStrategy, RoutedStrategyPool


class StrategyPerformanceFeedbackStore:
    def __init__(self, records: Optional[Iterable[StrategyPerformanceFeedback]] = None):
        self._records = {}
        for record in records or []:
            self.upsert(record)

    @classmethod
    def from_json(cls, path):
        path = Path(path)
        if not path.exists():
            return cls()
        payload = json.loads(path.read_text(encoding="utf-8"))
        records = [
            StrategyPerformanceFeedback.from_payload(item)
            for item in payload.get("records", [])
        ]
        return cls(records)

    def save_json(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "1.0",
            "records": [record.to_payload() for record in self.records],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @property
    def records(self):
        return tuple(self._records[key] for key in sorted(self._records))

    def upsert(self, record: StrategyPerformanceFeedback):
        self._records[self._key(record.strategy_id, record.symbol, record.regime)] = record

    def score_for(self, strategy_id: str, symbol: str, regime: RegimeKind, default: float = 0.5) -> float:
        candidates = (
            self._key(strategy_id, symbol, regime),
            self._key(strategy_id, symbol, "*"),
            self._key(strategy_id, "*", regime),
            self._key(strategy_id, "*", "*"),
        )
        for key in candidates:
            record = self._records.get(key)
            if record is not None:
                return float(record.performance_score)
        return default

    @staticmethod
    def _key(strategy_id, symbol, regime):
        regime_value = regime.value if hasattr(regime, "value") else str(regime)
        return (str(strategy_id), str(symbol).upper(), regime_value)


class MultiStrategyEvaluator:
    SCORING_CONTRACT_VERSION = "strategy_score_contract_v1"
    DEFAULT_WEIGHT_PROFILE = "balanced_signal_first_v1"
    DEFAULT_WEIGHTS = {
        "signal_quality_score": 0.35,
        "regime_fit_score": 0.20,
        "risk_score": 0.15,
        "liquidity_score": 0.10,
        "performance_score": 0.20,
    }
    DEFAULT_WEIGHT_RATIONALE = {
        "signal_quality_score": "primary deterministic strategy confidence",
        "regime_fit_score": "market-state route confidence",
        "risk_score": "symbol calibration risk suitability",
        "liquidity_score": "symbol calibration liquidity suitability",
        "performance_score": "bounded historical strategy feedback",
    }

    def __init__(
        self,
        *,
        min_score: float = 0.55,
        symbol_calibration: Optional[Mapping[str, Any]] = None,
        feedback_store: Optional[StrategyPerformanceFeedbackStore] = None,
        weights: Optional[Mapping[str, float]] = None,
        stateless_validator: Optional[StatelessStrategyValidator] = None,
        evaluator_id: str = "multi_strategy_evaluator",
        evaluator_version: str = "1.0.0",
    ):
        self.min_score = min_score
        self.symbol_calibration = dict(symbol_calibration or {})
        self.feedback_store = feedback_store or StrategyPerformanceFeedbackStore()
        self.weights = self._normalize_weights(weights)
        self.stateless_validator = stateless_validator or StatelessStrategyValidator()
        self.evaluator_id = evaluator_id
        self.evaluator_version = evaluator_version

    def evaluate(
        self,
        routed_pool,
        features,
        index: int,
        *,
        regime: Optional[RegimeSnapshot] = None,
        evaluation_id: Optional[str] = None,
        timestamp: Optional[int] = None,
    ) -> StrategyEvaluationResult:
        routed = self._routed_candidates(routed_pool)
        if not routed:
            raise ValueError("strategy evaluation requires at least one routed strategy")

        first_route = routed[0].route
        regime_kind = RegimeKind(first_route.regime)
        evaluation_id = evaluation_id or f"{self.evaluator_id}:{first_route.symbol}:{first_route.timestamp}:{index}"
        timestamp = first_route.timestamp if timestamp is None else timestamp

        candidate_payloads = []
        signals_by_strategy = {}
        for item in routed:
            validation = self.stateless_validator.validate(item.strategy, features, index)
            signal = validation.signal
            if isinstance(signal, StrategySignal):
                signal = self._signal_with_route_context(signal, item)
                signals_by_strategy[item.route.strategy_id] = signal
            candidate_payloads.append(
                self._candidate_payload(
                    evaluation_id=evaluation_id,
                    routed=item,
                    signal=signal,
                    validation=validation,
                    regime=regime_kind,
                )
            )

        eligible = [
            payload for payload in candidate_payloads
            if payload["candidate_status"] == "ELIGIBLE"
        ]
        eligible.sort(
            key=lambda payload: (
                -payload["adjusted_final_score"],
                payload["strategy_id"],
                payload.get("signal_id") or "",
            )
        )

        self._assign_score_ranks(candidate_payloads)
        for execution_rank, payload in enumerate(eligible, start=1):
            payload["execution_rank"] = execution_rank

        selected_payload = eligible[0] if eligible else None
        if selected_payload is not None:
            selected_payload["candidate_status"] = "SELECTED"
            for payload in eligible[1:]:
                payload["candidate_status"] = "REJECTED"
                payload["rejection_reasons"].append("lower_ranked_duplicate_order_guard")

        selected_signal = None
        selected_strategy_id = None
        selected_executable = False
        status = "NO_EXECUTABLE_SIGNAL"
        reason_codes = ["strategy_pool_evaluated"]
        if selected_payload is not None:
            selected_strategy_id = selected_payload["strategy_id"]
            selected_signal = signals_by_strategy[selected_strategy_id]
            selected_executable = True
            status = "SELECTED_EXECUTABLE"
            reason_codes.append("selected_executable_signal")
        else:
            selected_signal = self._best_observation_signal(
                evaluation_id,
                index,
                first_route,
                candidate_payloads,
                signals_by_strategy,
            )
            reason_codes.append("no_executable_strategy_signal")

        route_decision = self._route_decision_payload(routed_pool, routed)
        route_decision["scoring_contract"] = self.scoring_contract_payload()

        candidates = [
            StrategyCandidateScore.from_payload(payload)
            for payload in sorted(candidate_payloads, key=lambda item: item["score_rank"])
        ]
        return StrategyEvaluationResult(
            evaluation_id=evaluation_id,
            timestamp=timestamp,
            symbol=first_route.symbol,
            timeframe=first_route.timeframe,
            regime=regime_kind,
            status=status,
            selected_strategy_id=selected_strategy_id,
            selected_signal=selected_signal,
            selected_executable=selected_executable,
            candidates=candidates,
            reason_codes=reason_codes,
            route_decision=route_decision,
        )

    @staticmethod
    def _routed_candidates(routed_pool):
        if isinstance(routed_pool, RoutedStrategyPool):
            return list(routed_pool.strategies)
        if isinstance(routed_pool, RoutedStrategy):
            return [routed_pool]
        return list(routed_pool)

    @staticmethod
    def _signal_with_route_context(signal: StrategySignal, routed: RoutedStrategy) -> StrategySignal:
        payload = signal.to_payload()
        if payload.get("symbol") is None:
            payload["symbol"] = routed.route.symbol
        if payload.get("timeframe") is None:
            payload["timeframe"] = routed.route.timeframe
        reason_codes = list(payload.get("reason_codes") or [])
        for code in routed.route.reason_codes:
            if code not in reason_codes:
                reason_codes.append(code)
        payload["reason_codes"] = reason_codes
        return StrategySignal.from_payload(payload)

    def _candidate_payload(self, *, evaluation_id, routed, signal, validation, regime):
        route = routed.route
        strategy_id = route.strategy_id
        strategy_version = route.strategy_version
        signal_quality = float(getattr(signal, "confidence", None) or 0.0)
        regime_fit = float(route.confidence if route.confidence is not None else 0.5)
        calibration = self._calibration(strategy_id, route.symbol, regime)
        performance = self.feedback_store.score_for(strategy_id, route.symbol, regime, default=0.5)
        adjusted = self._adjusted_score(
            signal_quality_score=signal_quality,
            regime_fit_score=regime_fit,
            risk_score=calibration["risk_score"],
            liquidity_score=calibration["liquidity_score"],
            performance_score=performance,
            symbol_calibration_weight=calibration["symbol_calibration_weight"],
        )

        validation_errors = list(getattr(validation, "errors", ()) or ())
        orderable = isinstance(signal, StrategySignal) and signal.is_orderable
        status = "ELIGIBLE" if orderable and adjusted >= self.min_score else "REJECTED"
        rejection_reasons = []
        if validation_errors:
            status = "REJECTED"
            rejection_reasons.extend("stateless_validation_failed:" + error for error in validation_errors)
            orderable = False
        elif signal is None:
            status = "NO_SIGNAL"
            rejection_reasons.append("strategy_no_signal")
        elif not orderable:
            status = "OBSERVE_ONLY"
            rejection_reasons.append("non_orderable_signal")
        elif adjusted < self.min_score:
            rejection_reasons.append("score_below_threshold")

        return {
            "evaluation_id": evaluation_id,
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "signal_id": getattr(signal, "signal_id", None),
            "symbol": route.symbol,
            "timeframe": route.timeframe,
            "regime": regime.value,
            "action": getattr(signal, "action", None),
            "signal_type": getattr(signal, "signal_type", None),
            "orderable": orderable,
            "candidate_status": status,
            "signal_quality_score": self._clamp(signal_quality),
            "regime_fit_score": self._clamp(regime_fit),
            "risk_score": calibration["risk_score"],
            "liquidity_score": calibration["liquidity_score"],
            "performance_score": performance,
            "symbol_calibration_weight": calibration["symbol_calibration_weight"],
            "adjusted_final_score": adjusted,
            "rank": None,
            "score_rank": None,
            "execution_rank": None,
            "rejection_reasons": rejection_reasons,
            "validation_errors": validation_errors,
            "watch_plan": getattr(signal, "watch_plan", None),
        }

    def _calibration(self, strategy_id, symbol, regime):
        config = {
            "symbol_calibration_weight": 1.0,
            "risk_score": 1.0,
            "liquidity_score": 1.0,
        }
        regime_value = regime.value if hasattr(regime, "value") else str(regime)
        keys = (
            "default",
            str(symbol).upper(),
            f"{strategy_id}:{str(symbol).upper()}",
            f"{strategy_id}:{str(symbol).upper()}:{regime_value}",
        )
        for key in keys:
            raw = self.symbol_calibration.get(key)
            if raw is None:
                continue
            if isinstance(raw, Mapping):
                config.update(raw)
            else:
                config["symbol_calibration_weight"] = raw
        return {
            "symbol_calibration_weight": max(0.0, float(config["symbol_calibration_weight"])),
            "risk_score": self._clamp(config["risk_score"]),
            "liquidity_score": self._clamp(config["liquidity_score"]),
        }

    def _adjusted_score(self, **scores):
        base = 0.0
        for key, weight in self.weights.items():
            base += self._clamp(scores[key]) * float(weight)
        return self._clamp(base * float(scores["symbol_calibration_weight"]))

    def scoring_contract_payload(self):
        return {
            "schema_version": self.SCORING_CONTRACT_VERSION,
            "weight_profile": self.DEFAULT_WEIGHT_PROFILE,
            "weights": dict(self.weights),
            "weight_sum": sum(float(value) for value in self.weights.values()),
            "default_weights": dict(self.DEFAULT_WEIGHTS),
            "default_weight_rationale": dict(self.DEFAULT_WEIGHT_RATIONALE),
            "formula": "clamp(weighted_component_sum * symbol_calibration_weight)",
            "weighted_component_sum": "sum(clamp(component_score) * weight)",
            "component_score_fields": list(self.DEFAULT_WEIGHTS),
            "symbol_calibration_applied_after_weighted_sum": True,
            "clamp_range": [0.0, 1.0],
        }

    @classmethod
    def _normalize_weights(cls, weights: Optional[Mapping[str, float]] = None):
        normalized = {key: float(value) for key, value in cls.DEFAULT_WEIGHTS.items()}
        if weights:
            unknown = sorted(set(weights) - set(cls.DEFAULT_WEIGHTS))
            if unknown:
                raise ValueError(f"unknown strategy score weight keys: {', '.join(unknown)}")
            for key, value in weights.items():
                weight = float(value)
                if weight < 0:
                    raise ValueError("strategy score weights must be non-negative")
                normalized[key] = weight
        if sum(normalized.values()) <= 0:
            raise ValueError("strategy score weights must have positive total weight")
        return normalized

    def _best_observation_signal(
        self,
        evaluation_id,
        index,
        route,
        candidate_payloads,
        signals_by_strategy,
    ):
        observed = [
            payload for payload in candidate_payloads
            if payload["candidate_status"] == "OBSERVE_ONLY"
        ]
        if observed:
            observed.sort(
                key=lambda payload: (
                    -payload["adjusted_final_score"],
                    payload["strategy_id"],
                    payload.get("signal_id") or "",
                )
            )
            return signals_by_strategy[observed[0]["strategy_id"]]

        return StrategySignal(
            signal_id=f"{evaluation_id}:no_trade",
            strategy_id=self.evaluator_id,
            strategy_version=self.evaluator_version,
            action=StrategyAction.NO_TRADE,
            signal_type="NO_EXECUTABLE_SIGNAL",
            signal_index=index,
            symbol=route.symbol,
            timeframe=route.timeframe,
            confidence=0.0,
            reason_codes=["no_executable_strategy_signal"],
            trade_now=False,
            should_send_order=False,
            watch_plan={
                "candidate_count": len(candidate_payloads),
                "recheck_on": "next_closed_bar",
            },
        )

    @staticmethod
    def _route_decision_payload(routed_pool, routed):
        if isinstance(routed_pool, RoutedStrategyPool):
            return dict(routed_pool.decision)
        return {
            "schema_version": "1.0",
            "candidate_count": len(routed),
            "strategy_ids": [item.route.strategy_id for item in routed],
            "route_ids": [item.route.route_id for item in routed],
        }

    @staticmethod
    def _assign_score_ranks(candidate_payloads):
        ordered = sorted(
            candidate_payloads,
            key=lambda item: (
                -item["adjusted_final_score"],
                item["strategy_id"],
                item.get("signal_id") or "",
            ),
        )
        for rank, item in enumerate(ordered, start=1):
            item["score_rank"] = rank
            item["rank"] = rank

    @staticmethod
    def _clamp(value):
        return max(0.0, min(1.0, float(value)))
