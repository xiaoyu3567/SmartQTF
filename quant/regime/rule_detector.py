from typing import Mapping, Optional

from quant.schemas import (
    FeatureSnapshot,
    RegimeKind,
    RegimeSnapshot,
    RegimeThresholdConfig,
    ResolvedRegimeThresholds,
)


class RuleBasedRegimeDetector:
    def __init__(
        self,
        *,
        detector_id: str = "rule_based_regime",
        detector_version: str = "1.0.0",
        trend_threshold: float = 0.01,
        volatility_threshold: float = 0.03,
        threshold_config: Optional[RegimeThresholdConfig] = None,
    ):
        if trend_threshold < 0:
            raise ValueError("trend_threshold must be >= 0")
        if volatility_threshold < 0:
            raise ValueError("volatility_threshold must be >= 0")
        self._validate_threshold_config(
            threshold_config=threshold_config,
            detector_id=detector_id,
            detector_version=detector_version,
        )

        self.detector_id = detector_id
        self.detector_version = detector_version
        self.trend_threshold = trend_threshold
        self.volatility_threshold = volatility_threshold
        self.threshold_config = threshold_config

    def detect(self, snapshot: FeatureSnapshot, *, quality_report=None) -> RegimeSnapshot:
        self._validate_snapshot_time_bounds(snapshot)
        resolved_thresholds = self._resolve_thresholds(snapshot)
        trend_threshold, volatility_threshold = resolved_thresholds.thresholds.rule_thresholds(
            trend_threshold=self.trend_threshold,
            volatility_threshold=self.volatility_threshold,
        )
        gate_reasons = self._quality_gate_reasons(snapshot, quality_report)
        if gate_reasons:
            return self._quality_gated_unknown(
                snapshot,
                quality_report,
                gate_reasons,
                resolved_thresholds=resolved_thresholds,
            )

        trend_direction_score = self._trend_direction_score(snapshot.values)
        trend_score = 0.0 if trend_direction_score is None else abs(trend_direction_score)
        volatility_value = self._volatility_value(snapshot.values)
        volatility_score = 0.0 if volatility_value is None else abs(volatility_value)
        score_bundle = self._score_bundle(
            snapshot.values,
            trend_direction_score=trend_direction_score,
            trend_threshold=trend_threshold,
            volatility_score=volatility_score,
            volatility_threshold=volatility_threshold,
        )
        direction = self._direction_from_trend_score(
            trend_direction_score, trend_threshold
        )
        volatility_state = self._volatility_state(
            None if volatility_value is None else volatility_score,
            volatility_threshold,
        )
        tradability = self._tradability_for(direction, volatility_state)

        if volatility_score >= volatility_threshold:
            regime = self._fine_grained_regime(direction, volatility_state, score_bundle["scores"])
            reason_codes = [
                "volatility_threshold_exceeded",
                "regime_score:volatility",
            ]
            confidence = self._bounded_confidence(volatility_score, volatility_threshold)
        elif trend_score >= trend_threshold:
            regime = self._fine_grained_regime(direction, volatility_state, score_bundle["scores"])
            reason_codes = ["trend_threshold_exceeded", "regime_score:trend"]
            confidence = self._bounded_confidence(trend_score, trend_threshold)
        else:
            regime = self._fine_grained_regime(direction, volatility_state, score_bundle["scores"])
            reason_codes = [
                "no_trend_or_volatility_threshold",
                "regime_score:range",
            ]
            confidence = 0.55
        reason_codes.extend(self._score_reason_codes(score_bundle["score_inputs"], reason_codes))
        metrics = {
            "trend_score": trend_score,
            "trend_threshold": trend_threshold,
            "volatility_score": volatility_score,
            "volatility_threshold": volatility_threshold,
        }
        self._add_threshold_metrics(metrics, resolved_thresholds)
        if trend_direction_score is not None:
            metrics["trend_direction_score"] = trend_direction_score
        reasons = self._human_reasons(
            reason_codes,
            regime=regime,
            direction=direction,
            volatility_state=volatility_state,
            tradability=tradability,
            metrics=metrics,
            scores=score_bundle["scores"],
            score_inputs=score_bundle["score_inputs"],
        )

        return RegimeSnapshot(
            regime_id=f"{self.detector_id}:{snapshot.snapshot_id}",
            timestamp=snapshot.timestamp,
            symbol=snapshot.symbol,
            timeframe=snapshot.timeframe,
            as_of_timestamp=snapshot.as_of_timestamp,
            detector_id=self.detector_id,
            detector_version=self.detector_version,
            regime=regime,
            confidence=confidence,
            reason_codes=reason_codes,
            reasons=reasons,
            metrics=metrics,
            scores=score_bundle["scores"],
            score_inputs=score_bundle["score_inputs"],
            source_window_start=snapshot.source_window_start,
            source_window_end=snapshot.source_window_end,
            input_refs=self._input_refs(
                snapshot,
                quality_report,
                resolved_thresholds=resolved_thresholds,
            ),
            threshold_version=resolved_thresholds.threshold_version,
            threshold_scope=resolved_thresholds.scope,
            direction=direction,
            volatility_state=volatility_state,
            tradability=tradability,
            trace=snapshot.trace,
        )

    @classmethod
    def _trend_score(cls, values: Mapping[str, object]) -> float:
        signed_score = cls._trend_direction_score(values)
        if signed_score is None:
            return 0.0
        return abs(signed_score)

    @classmethod
    def _trend_direction_score(cls, values: Mapping[str, object]) -> Optional[float]:
        for key in (
            "trend_strength",
            "ema_spread",
            "ema_spread_pct",
            "return",
            "return_pct",
        ):
            explicit_score = cls._numeric(values.get(key))
            if explicit_score is not None:
                return explicit_score

        fast_ma = cls._numeric(values.get("ma_fast"))
        slow_ma = cls._numeric(values.get("ma_slow"))
        if fast_ma is not None and slow_ma is not None and slow_ma != 0:
            return (fast_ma - slow_ma) / slow_ma

        rsi = cls._numeric(values.get("rsi"))
        if rsi is not None:
            return (rsi - 50.0) / 50.0
        return None

    @classmethod
    def _volatility_score(cls, values: Mapping[str, object]) -> float:
        value = cls._volatility_value(values)
        if value is None:
            return 0.0
        return abs(value)

    @classmethod
    def _volatility_value(cls, values: Mapping[str, object]) -> Optional[float]:
        for key in ("volatility", "atr_pct", "atr_percent", "average_true_range_pct"):
            value = cls._numeric(values.get(key))
            if value is not None:
                return value
        return None

    @staticmethod
    def _numeric(value: object) -> Optional[float]:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None

    @classmethod
    def _numeric_from_keys(
        cls,
        values: Mapping[str, object],
        keys: tuple[str, ...],
    ) -> Optional[float]:
        for key in keys:
            value = cls._numeric(values.get(key))
            if value is not None:
                return value
        return None

    @staticmethod
    def _bounded_confidence(score: float, threshold: float) -> float:
        if threshold == 0:
            return 1.0
        return max(0.5, min(0.95, score / (threshold * 2.0)))

    @staticmethod
    def _direction_from_trend_score(
        signed_score: Optional[float],
        threshold: float,
    ) -> str:
        if signed_score is None:
            return "unknown"
        if abs(signed_score) < threshold:
            return "neutral"
        if signed_score > 0:
            return "bullish"
        if signed_score < 0:
            return "bearish"
        return "neutral"

    @staticmethod
    def _volatility_state(
        volatility_score: Optional[float],
        threshold: float,
    ) -> str:
        if volatility_score is None:
            return "unknown"
        score = abs(volatility_score)
        if threshold == 0:
            return "high" if score > 0 else "normal"
        if score >= threshold * 2.0:
            return "extreme"
        if score >= threshold:
            return "high"
        if score < threshold * 0.5:
            return "low"
        return "normal"

    @staticmethod
    def _tradability_for(direction: str, volatility_state: str) -> str:
        if direction == "unknown" or volatility_state == "unknown":
            return "observe_only"
        if volatility_state == "extreme":
            return "observe_only"
        return "tradable"

    @classmethod
    def _score_bundle(
        cls,
        values: Mapping[str, object],
        *,
        trend_direction_score: Optional[float],
        trend_threshold: float,
        volatility_score: float,
        volatility_threshold: float,
    ) -> dict[str, object]:
        trend_input = cls._trend_score_input(values, trend_direction_score)
        volatility_input = cls._volatility_score_input(values, volatility_score)
        liquidity_score, liquidity_input = cls._liquidity_activity_score(values)
        orderflow_score, orderflow_input = cls._orderflow_score(values)
        return {
            "scores": {
                "trend": cls._normalize_score(
                    0.0 if trend_direction_score is None else abs(trend_direction_score),
                    trend_threshold,
                ),
                "volatility": cls._normalize_score(volatility_score, volatility_threshold),
                "liquidity_activity": liquidity_score,
                "orderflow": orderflow_score,
            },
            "score_inputs": {
                "trend": trend_input,
                "volatility": volatility_input,
                "liquidity_activity": liquidity_input,
                "orderflow": orderflow_input,
            },
        }

    @staticmethod
    def _empty_scores() -> dict[str, float]:
        return {
            "trend": 0.0,
            "volatility": 0.0,
            "liquidity_activity": 0.0,
            "orderflow": 0.0,
        }

    @classmethod
    def _normalize_score(cls, score: float, threshold: float) -> float:
        if threshold <= 0:
            return 1.0 if score > 0 else 0.0
        return cls._clamp01(score / threshold)

    @classmethod
    def _trend_score_input(
        cls,
        values: Mapping[str, object],
        trend_direction_score: Optional[float],
    ) -> dict[str, object]:
        keys = (
            "trend_strength",
            "return",
            "return_pct",
            "ema_spread",
            "ema_spread_pct",
            "ma_fast",
            "ma_slow",
            "rsi",
            "adx",
        )
        return {
            "fields": cls._present_fields(values, keys),
            "signed_score": trend_direction_score,
            "missing": trend_direction_score is None,
        }

    @classmethod
    def _volatility_score_input(
        cls,
        values: Mapping[str, object],
        volatility_score: float,
    ) -> dict[str, object]:
        keys = ("volatility", "atr_pct", "atr_percent", "average_true_range_pct", "atr", "close")
        return {
            "fields": cls._present_fields(values, keys),
            "score_source_value": volatility_score,
            "missing": not cls._present_fields(values, keys),
        }

    @classmethod
    def _liquidity_activity_score(
        cls,
        values: Mapping[str, object],
    ) -> tuple[float, dict[str, object]]:
        volume_z = cls._numeric_from_keys(
            values,
            ("volume_z", "volume_zscore", "volume.zscore", "volume_z_score"),
        )
        turnover_z = cls._numeric_from_keys(
            values,
            (
                "turnover_z",
                "turnover_zscore",
                "quote_volume_z",
                "market_structure.volume_z",
            ),
        )
        raw_score = None
        if volume_z is not None:
            raw_score = abs(volume_z) / 3.0
        elif turnover_z is not None:
            raw_score = abs(turnover_z) / 3.0

        fields = cls._present_fields(
            values,
            (
                "volume_z",
                "volume_zscore",
                "volume.zscore",
                "volume_z_score",
                "turnover_z",
                "turnover_zscore",
                "quote_volume_z",
                "market_structure.volume_z",
                "market_structure.liquidity_range_width",
            ),
        )
        return cls._clamp01(raw_score or 0.0), {
            "fields": fields,
            "volume_z": volume_z,
            "turnover_z": turnover_z,
            "missing": raw_score is None,
        }

    @classmethod
    def _orderflow_score(
        cls,
        values: Mapping[str, object],
    ) -> tuple[float, dict[str, object]]:
        buy_volume = cls._numeric_from_keys(values, ("orderflow.buy_volume", "buy_volume"))
        sell_volume = cls._numeric_from_keys(values, ("orderflow.sell_volume", "sell_volume"))
        taker_buy_ratio = cls._numeric_from_keys(
            values,
            (
                "orderflow.taker_buy_sell_ratio",
                "taker_buy_sell_ratio",
                "taker_buy_ratio",
            ),
        )
        imbalance = cls._numeric_from_keys(
            values,
            (
                "orderflow.imbalance",
                "orderflow_imbalance",
                "order_flow_imbalance",
            ),
        )
        orderbook_imbalance = cls._numeric_from_keys(
            values,
            (
                "orderflow.orderbook_imbalance",
                "orderbook_imbalance",
            ),
        )

        directional_scores = []
        if buy_volume is not None and sell_volume is not None and buy_volume + sell_volume > 0:
            directional_scores.append((buy_volume - sell_volume) / (buy_volume + sell_volume))
        if taker_buy_ratio is not None and taker_buy_ratio >= 0:
            directional_scores.append((taker_buy_ratio - 1.0) / (taker_buy_ratio + 1.0))
        if imbalance is not None:
            directional_scores.append(cls._bounded_signed_ratio(imbalance))
        if orderbook_imbalance is not None:
            directional_scores.append(orderbook_imbalance)

        score = max((abs(value) for value in directional_scores), default=0.0)
        fields = cls._present_fields(
            values,
            (
                "orderflow.buy_volume",
                "orderflow.sell_volume",
                "orderflow.taker_buy_sell_ratio",
                "orderflow.imbalance",
                "orderflow.orderbook_imbalance",
                "taker_buy_ratio",
                "orderflow_imbalance",
                "order_flow_imbalance",
            ),
        )
        return cls._clamp01(score), {
            "fields": fields,
            "buy_volume": buy_volume,
            "sell_volume": sell_volume,
            "taker_buy_ratio": taker_buy_ratio,
            "imbalance": imbalance,
            "orderbook_imbalance": orderbook_imbalance,
            "missing": not directional_scores,
        }

    @staticmethod
    def _bounded_signed_ratio(value: float) -> float:
        absolute = abs(value)
        if absolute <= 1.0:
            return value
        return value / (absolute + 1.0)

    @classmethod
    def _fine_grained_regime(
        cls,
        direction: str,
        volatility_state: str,
        scores: Mapping[str, float],
    ) -> RegimeKind:
        volatility_bucket = cls._volatility_bucket_from_score(scores["volatility"])
        if direction == "bullish" and scores["trend"] >= 1.0:
            return {
                "high": RegimeKind.UPTREND_HIGH_VOL,
                "normal": RegimeKind.UPTREND_NORMAL_VOL,
                "low": RegimeKind.UPTREND_LOW_VOL,
            }[volatility_bucket]
        if direction == "bearish" and scores["trend"] >= 1.0:
            return {
                "high": RegimeKind.DOWNTREND_HIGH_VOL,
                "normal": RegimeKind.DOWNTREND_NORMAL_VOL,
                "low": RegimeKind.DOWNTREND_LOW_VOL,
            }[volatility_bucket]
        if (
            volatility_state == "extreme"
            and scores["liquidity_activity"] >= 0.8
            and scores["orderflow"] >= 0.8
        ):
            return RegimeKind.CHAOS
        return {
            "high": RegimeKind.RANGE_HIGH_VOL,
            "normal": RegimeKind.RANGE_NORMAL_VOL,
            "low": RegimeKind.RANGE_LOW_VOL,
        }[volatility_bucket]

    @staticmethod
    def _volatility_bucket_from_score(score: float) -> str:
        if score >= 1.0:
            return "high"
        if score <= 0.5:
            return "low"
        return "normal"

    @staticmethod
    def _clamp01(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    @staticmethod
    def _present_fields(
        values: Mapping[str, object],
        keys: tuple[str, ...],
    ) -> dict[str, object]:
        return {
            key: values[key]
            for key in keys
            if key in values and values[key] is not None
        }

    @staticmethod
    def _score_reason_codes(
        score_inputs: Mapping[str, object],
        existing_reason_codes: Optional[list[str]] = None,
    ) -> list[str]:
        existing = set(existing_reason_codes or [])
        reason_codes = []
        for score_name in ("trend", "volatility", "liquidity_activity", "orderflow"):
            score_input = score_inputs.get(score_name, {})
            if not isinstance(score_input, Mapping):
                continue
            base_code = f"regime_score:{score_name}"
            if score_input.get("missing"):
                if score_name in {"liquidity_activity", "orderflow"}:
                    reason_codes.append(f"{base_code}:missing")
                continue
            fields = score_input.get("fields")
            if (
                isinstance(fields, Mapping)
                and fields
                and base_code not in existing
                and base_code != "regime_score:range"
            ):
                reason_codes.append(f"{base_code}:observed")
        return reason_codes

    @classmethod
    def _human_reasons(
        cls,
        reason_codes: list[str],
        *,
        regime: RegimeKind,
        direction: str,
        volatility_state: str,
        tradability: str,
        metrics: Mapping[str, object],
        scores: Mapping[str, float],
        score_inputs: Mapping[str, object],
    ) -> list[str]:
        return [
            cls._human_reason_for_code(
                reason_code,
                regime=regime,
                direction=direction,
                volatility_state=volatility_state,
                tradability=tradability,
                metrics=metrics,
                scores=scores,
                score_inputs=score_inputs,
            )
            for reason_code in reason_codes
        ]

    @classmethod
    def _human_reason_for_code(
        cls,
        reason_code: str,
        *,
        regime: RegimeKind,
        direction: str,
        volatility_state: str,
        tradability: str,
        metrics: Mapping[str, object],
        scores: Mapping[str, float],
        score_inputs: Mapping[str, object],
    ) -> str:
        regime_value = cls._safe_label(getattr(regime, "value", regime))
        if reason_code == "trend_threshold_exceeded":
            return (
                "Trend score "
                f"{cls._format_metric(metrics.get('trend_score'))} met threshold "
                f"{cls._format_metric(metrics.get('trend_threshold'))}; "
                f"direction is {direction}."
            )
        if reason_code == "volatility_threshold_exceeded":
            return (
                "Volatility score "
                f"{cls._format_metric(metrics.get('volatility_score'))} met threshold "
                f"{cls._format_metric(metrics.get('volatility_threshold'))}; "
                f"volatility state is {volatility_state}."
            )
        if reason_code == "adx_trend_threshold_exceeded":
            return (
                "ADX score "
                f"{cls._format_metric(metrics.get('adx'))} met threshold "
                f"{cls._format_metric(metrics.get('adx_trend_threshold'))}; "
                f"direction is {direction}."
            )
        if reason_code == "atr_volatility_threshold_exceeded":
            return (
                "ATR percent "
                f"{cls._format_metric(metrics.get('atr_pct'))} met threshold "
                f"{cls._format_metric(metrics.get('atr_pct_volatility_threshold'))}; "
                f"volatility state is {volatility_state}."
            )
        if reason_code == "no_trend_or_volatility_threshold":
            return (
                "Neither trend nor volatility reached its threshold, so the detector "
                f"selected {regime_value}."
            )
        if reason_code == "adx_and_atr_thresholds_not_met":
            return (
                "ADX and ATR percent were both below configured thresholds, so the detector "
                f"selected {regime_value}."
            )
        if reason_code == "regime_score:trend":
            return (
                "Trend was the primary regime driver "
                f"(normalized trend score {cls._format_metric(scores.get('trend'))}; "
                f"fields: {cls._score_field_names(score_inputs.get('trend'))})."
            )
        if reason_code == "regime_score:volatility":
            return (
                "Volatility was the primary regime driver "
                f"(normalized volatility score {cls._format_metric(scores.get('volatility'))}; "
                f"fields: {cls._score_field_names(score_inputs.get('volatility'))})."
            )
        if reason_code == "regime_score:range":
            return (
                "Trend and volatility scores stayed below action thresholds "
                f"(trend {cls._format_metric(scores.get('trend'))}, "
                f"volatility {cls._format_metric(scores.get('volatility'))})."
            )
        if reason_code == "regime_score:trend:observed":
            return (
                "Trend evidence was observed "
                f"(normalized trend score {cls._format_metric(scores.get('trend'))}; "
                f"fields: {cls._score_field_names(score_inputs.get('trend'))})."
            )
        if reason_code == "regime_score:volatility:observed":
            return (
                "Volatility evidence was observed "
                f"(normalized volatility score {cls._format_metric(scores.get('volatility'))}; "
                f"fields: {cls._score_field_names(score_inputs.get('volatility'))})."
            )
        if reason_code == "regime_score:liquidity_activity:observed":
            return (
                "Liquidity activity evidence was observed "
                f"(normalized liquidity score {cls._format_metric(scores.get('liquidity_activity'))}; "
                f"fields: {cls._score_field_names(score_inputs.get('liquidity_activity'))})."
            )
        if reason_code == "regime_score:orderflow:observed":
            return (
                "Orderflow evidence was observed "
                f"(normalized orderflow score {cls._format_metric(scores.get('orderflow'))}; "
                f"fields: {cls._score_field_names(score_inputs.get('orderflow'))})."
            )
        if reason_code == "regime_score:liquidity_activity:missing":
            return (
                "Liquidity activity inputs were missing, so liquidity activity score "
                "defaulted to 0."
            )
        if reason_code == "regime_score:orderflow:missing":
            return "Orderflow inputs were missing, so orderflow score defaulted to 0."
        if reason_code == "regime_quality_gate_blocked":
            return (
                "Regime quality gate blocked normal classification and set "
                f"tradability to {tradability}."
            )
        if reason_code == "quality_report_failed":
            return "Data quality report did not pass, so normal regime classification was blocked."
        if reason_code == "feature_snapshot_incomplete_bar":
            return "Feature snapshot used an incomplete bar, so normal regime classification was blocked."
        if reason_code == "feature_snapshot_includes_incomplete_last_bar":
            return "Feature snapshot explicitly included an incomplete last bar, so normal classification was blocked."
        if reason_code.startswith("quality_issue:"):
            issue_code = cls._safe_label(reason_code.split(":", 1)[1])
            return f"Quality issue {issue_code} contributed to the quality gate block."
        return (
            "Reason code "
            f"{cls._safe_label(reason_code)} contributed to {regime_value} classification."
        )

    @staticmethod
    def _format_metric(value: object) -> str:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "unknown"
        return f"{float(value):.4g}"

    @staticmethod
    def _score_field_names(score_input: object) -> str:
        if not isinstance(score_input, Mapping):
            return "none"
        fields = score_input.get("fields")
        if not isinstance(fields, Mapping) or not fields:
            return "none"
        return ", ".join(str(key) for key in fields)

    @staticmethod
    def _safe_label(value: object) -> str:
        raw = str(value)
        lowered = raw.lower()
        forbidden = (
            "api_key",
            "api-key",
            "api_secret",
            "api secret",
            "secret",
            "passphrase",
            "bearer",
            "private key",
            "raw response",
            "raw_exchange_response",
            "account_id",
        )
        if any(token in lowered for token in forbidden):
            return "redacted"
        cleaned = "".join(
            char if char.isalnum() or char in ":_-" else "_"
            for char in raw
        ).strip("_")
        return cleaned[:80] or "unknown"

    @staticmethod
    def _validate_snapshot_time_bounds(snapshot: FeatureSnapshot) -> None:
        if snapshot.as_of_timestamp > snapshot.timestamp:
            raise ValueError("as_of_timestamp must be <= timestamp")
        if (
            snapshot.source_window_end is not None
            and snapshot.source_window_end > snapshot.as_of_timestamp
        ):
            raise ValueError("source_window_end must be <= as_of_timestamp")

    @classmethod
    def _quality_gate_reasons(cls, snapshot: FeatureSnapshot, quality_report) -> list[str]:
        reasons = []
        if quality_report is not None and getattr(quality_report, "passed", False) is False:
            reasons.append("quality_report_failed")

        if getattr(snapshot, "is_complete_bar", True) is False:
            reasons.append("feature_snapshot_incomplete_bar")

        if getattr(snapshot, "include_incomplete_last_bar", False) is True:
            reasons.append("feature_snapshot_includes_incomplete_last_bar")

        return reasons

    @classmethod
    def _quality_gated_unknown(
        cls,
        snapshot: FeatureSnapshot,
        quality_report,
        gate_reasons: list[str],
        *,
        resolved_thresholds: Optional[ResolvedRegimeThresholds] = None,
    ) -> RegimeSnapshot:
        reason_codes = ["regime_quality_gate_blocked", *gate_reasons]
        if quality_report is not None:
            for issue in getattr(quality_report, "issues", []) or []:
                code = getattr(issue, "code", None)
                code_value = getattr(code, "value", code)
                if code_value:
                    reason_codes.append(f"quality_issue:{code_value}")
        metrics = cls._quality_gate_metrics(resolved_thresholds)
        score_inputs = {"quality_gate": gate_reasons}
        reasons = cls._human_reasons(
            reason_codes,
            regime=RegimeKind.UNKNOWN,
            direction="unknown",
            volatility_state="unknown",
            tradability="avoid",
            metrics=metrics,
            scores=cls._empty_scores(),
            score_inputs=score_inputs,
        )

        return RegimeSnapshot(
            regime_id=f"quality_gate:{snapshot.snapshot_id}",
            timestamp=snapshot.timestamp,
            symbol=snapshot.symbol,
            timeframe=snapshot.timeframe,
            as_of_timestamp=snapshot.as_of_timestamp,
            detector_id="regime_quality_gate",
            detector_version="1.0.0",
            regime=RegimeKind.UNKNOWN,
            confidence=0.0,
            reason_codes=reason_codes,
            reasons=reasons,
            metrics=metrics,
            scores=cls._empty_scores(),
            score_inputs=score_inputs,
            source_window_start=snapshot.source_window_start,
            source_window_end=snapshot.source_window_end,
            input_refs=cls._input_refs(
                snapshot,
                quality_report,
                resolved_thresholds=resolved_thresholds,
            ),
            threshold_version=(
                resolved_thresholds.threshold_version
                if resolved_thresholds is not None
                else None
            ),
            threshold_scope=(
                resolved_thresholds.scope if resolved_thresholds is not None else None
            ),
            direction="unknown",
            volatility_state="unknown",
            tradability="avoid",
            trace=snapshot.trace,
        )

    @staticmethod
    def _input_refs(
        snapshot: FeatureSnapshot,
        quality_report,
        *,
        resolved_thresholds: Optional[ResolvedRegimeThresholds] = None,
    ) -> dict[str, object]:
        refs = {
            "feature_snapshot_id": snapshot.snapshot_id,
            "feature_set_id": snapshot.feature_set_id,
            "feature_set_version": snapshot.feature_set_version,
            "source_window_start": snapshot.source_window_start,
            "source_window_end": snapshot.source_window_end,
            "as_of_timestamp": snapshot.as_of_timestamp,
            "is_complete_bar": snapshot.is_complete_bar,
        }
        if resolved_thresholds is not None:
            refs["threshold_config"] = {
                "threshold_version": resolved_thresholds.threshold_version,
                "detector_id": resolved_thresholds.detector_id,
                "detector_version": resolved_thresholds.detector_version,
                "scope": resolved_thresholds.scope,
                "symbol": resolved_thresholds.symbol,
                "timeframe": resolved_thresholds.timeframe,
                "thresholds": resolved_thresholds.thresholds.to_payload(),
            }
        if quality_report is not None:
            quality_report_id = (
                f"quality:{quality_report.symbol}:{quality_report.timeframe}:"
                f"{quality_report.checked_count}:"
                f"{snapshot.source_window_start}:{snapshot.source_window_end}"
            )
            refs["quality_report_id"] = quality_report_id
            refs["quality_report"] = {
                "symbol": quality_report.symbol,
                "timeframe": quality_report.timeframe,
                "passed": quality_report.passed,
                "checked_count": quality_report.checked_count,
                "issue_codes": [
                    getattr(getattr(issue, "code", None), "value", getattr(issue, "code", None))
                    for issue in (getattr(quality_report, "issues", []) or [])
                ],
                "included_incomplete_bar": getattr(
                    quality_report, "included_incomplete_bar", False
                ),
                "incomplete_bar_timestamp": getattr(
                    quality_report, "incomplete_bar_timestamp", None
                ),
            }
        return refs

    def _resolve_thresholds(self, snapshot: FeatureSnapshot) -> ResolvedRegimeThresholds:
        if self.threshold_config is None:
            return RegimeThresholdConfig(
                threshold_version=f"{self.detector_id}:{self.detector_version}:default",
                detector_id=self.detector_id,
                detector_version=self.detector_version,
            ).resolve(symbol=snapshot.symbol, timeframe=snapshot.timeframe)
        return self.threshold_config.resolve(
            symbol=snapshot.symbol,
            timeframe=snapshot.timeframe,
        )

    @staticmethod
    def _validate_threshold_config(
        *,
        threshold_config: Optional[RegimeThresholdConfig],
        detector_id: str,
        detector_version: str,
    ) -> None:
        if threshold_config is None:
            return
        if threshold_config.detector_id not in (None, detector_id):
            raise ValueError("threshold_config detector_id does not match detector")
        if threshold_config.detector_version not in (None, detector_version):
            raise ValueError("threshold_config detector_version does not match detector")

    @staticmethod
    def _add_threshold_metrics(
        metrics: dict[str, float],
        resolved_thresholds: ResolvedRegimeThresholds,
    ) -> None:
        metrics["threshold_scope_code"] = {
            "default": 0.0,
            "timeframe": 1.0,
            "symbol": 2.0,
            "symbol_timeframe": 3.0,
        }[resolved_thresholds.scope]

    @classmethod
    def _quality_gate_metrics(
        cls,
        resolved_thresholds: Optional[ResolvedRegimeThresholds],
    ) -> dict[str, float]:
        metrics = {"quality_gate_passed": 0.0}
        if resolved_thresholds is not None:
            cls._add_threshold_metrics(metrics, resolved_thresholds)
        return metrics


class AdxAtrRegimeDetector(RuleBasedRegimeDetector):
    def __init__(
        self,
        *,
        detector_id: str = "adx_atr_regime",
        detector_version: str = "1.0.0",
        adx_trend_threshold: float = 25.0,
        atr_pct_volatility_threshold: float = 0.04,
        threshold_config: Optional[RegimeThresholdConfig] = None,
    ):
        if adx_trend_threshold < 0:
            raise ValueError("adx_trend_threshold must be >= 0")
        if atr_pct_volatility_threshold < 0:
            raise ValueError("atr_pct_volatility_threshold must be >= 0")

        super().__init__(
            detector_id=detector_id,
            detector_version=detector_version,
            trend_threshold=adx_trend_threshold,
            volatility_threshold=atr_pct_volatility_threshold,
            threshold_config=threshold_config,
        )
        self.adx_trend_threshold = adx_trend_threshold
        self.atr_pct_volatility_threshold = atr_pct_volatility_threshold

    def detect(self, snapshot: FeatureSnapshot, *, quality_report=None) -> RegimeSnapshot:
        self._validate_snapshot_time_bounds(snapshot)
        resolved_thresholds = self._resolve_thresholds(snapshot)
        (
            adx_trend_threshold,
            atr_pct_volatility_threshold,
        ) = resolved_thresholds.thresholds.adx_atr_thresholds(
            adx_trend_threshold=self.adx_trend_threshold,
            atr_pct_volatility_threshold=self.atr_pct_volatility_threshold,
        )
        gate_reasons = self._quality_gate_reasons(snapshot, quality_report)
        if gate_reasons:
            return self._quality_gated_unknown(
                snapshot,
                quality_report,
                gate_reasons,
                resolved_thresholds=resolved_thresholds,
            )

        adx_value = self._numeric_from_keys(
            snapshot.values,
            ("adx", "average_directional_index"),
        )
        adx_score = 0.0 if adx_value is None else abs(adx_value)
        atr_pct_value = self._atr_pct_value(snapshot.values)
        atr_pct_score = 0.0 if atr_pct_value is None else abs(atr_pct_value)
        metrics = {
            "adx": adx_score,
            "adx_trend_threshold": adx_trend_threshold,
            "atr_pct": atr_pct_score,
            "atr_pct_volatility_threshold": atr_pct_volatility_threshold,
        }
        self._add_threshold_metrics(metrics, resolved_thresholds)

        plus_di = self._numeric_from_keys(
            snapshot.values,
            ("plus_di", "positive_di", "di_plus"),
        )
        minus_di = self._numeric_from_keys(
            snapshot.values,
            ("minus_di", "negative_di", "di_minus"),
        )
        if plus_di is not None:
            metrics["plus_di"] = plus_di
        if minus_di is not None:
            metrics["minus_di"] = minus_di
        direction = self._direction_from_di(
            plus_di=plus_di,
            minus_di=minus_di,
            adx_score=adx_score,
            has_adx_input=adx_value is not None,
            adx_trend_threshold=adx_trend_threshold,
        )
        volatility_state = self._volatility_state(
            None if atr_pct_value is None else atr_pct_score,
            atr_pct_volatility_threshold,
        )
        score_bundle = self._score_bundle(
            snapshot.values,
            trend_direction_score=self._signed_adx_score(
                adx_score=adx_score,
                plus_di=plus_di,
                minus_di=minus_di,
                has_adx_input=adx_value is not None,
            ),
            trend_threshold=adx_trend_threshold,
            volatility_score=atr_pct_score,
            volatility_threshold=atr_pct_volatility_threshold,
        )
        tradability = self._tradability_for(direction, volatility_state)

        if atr_pct_score >= atr_pct_volatility_threshold:
            regime = self._fine_grained_regime(direction, volatility_state, score_bundle["scores"])
            reason_codes = [
                "atr_volatility_threshold_exceeded",
                "regime_score:volatility",
            ]
            confidence = self._bounded_confidence(
                atr_pct_score, atr_pct_volatility_threshold
            )
        elif adx_score >= adx_trend_threshold:
            regime = self._fine_grained_regime(direction, volatility_state, score_bundle["scores"])
            reason_codes = ["adx_trend_threshold_exceeded", "regime_score:trend"]
            confidence = self._bounded_confidence(adx_score, adx_trend_threshold)
        else:
            regime = self._fine_grained_regime(direction, volatility_state, score_bundle["scores"])
            reason_codes = [
                "adx_and_atr_thresholds_not_met",
                "regime_score:range",
            ]
            confidence = 0.55
        reason_codes.extend(self._score_reason_codes(score_bundle["score_inputs"], reason_codes))
        reasons = self._human_reasons(
            reason_codes,
            regime=regime,
            direction=direction,
            volatility_state=volatility_state,
            tradability=tradability,
            metrics=metrics,
            scores=score_bundle["scores"],
            score_inputs=score_bundle["score_inputs"],
        )

        return RegimeSnapshot(
            regime_id=f"{self.detector_id}:{snapshot.snapshot_id}",
            timestamp=snapshot.timestamp,
            symbol=snapshot.symbol,
            timeframe=snapshot.timeframe,
            as_of_timestamp=snapshot.as_of_timestamp,
            detector_id=self.detector_id,
            detector_version=self.detector_version,
            regime=regime,
            confidence=confidence,
            reason_codes=reason_codes,
            reasons=reasons,
            metrics=metrics,
            scores=score_bundle["scores"],
            score_inputs=score_bundle["score_inputs"],
            source_window_start=snapshot.source_window_start,
            source_window_end=snapshot.source_window_end,
            input_refs=self._input_refs(
                snapshot,
                quality_report,
                resolved_thresholds=resolved_thresholds,
            ),
            threshold_version=resolved_thresholds.threshold_version,
            threshold_scope=resolved_thresholds.scope,
            direction=direction,
            volatility_state=volatility_state,
            tradability=tradability,
            trace=snapshot.trace,
        )

    @classmethod
    def _adx_score(cls, values: Mapping[str, object]) -> float:
        value = cls._numeric_from_keys(values, ("adx", "average_directional_index"))
        if value is None:
            return 0.0
        return abs(value)

    @classmethod
    def _atr_pct_score(cls, values: Mapping[str, object]) -> float:
        value = cls._atr_pct_value(values)
        if value is None:
            return 0.0
        return abs(value)

    @classmethod
    def _atr_pct_value(cls, values: Mapping[str, object]) -> Optional[float]:
        explicit = cls._numeric_from_keys(
            values,
            ("atr_pct", "atr_percent", "average_true_range_pct"),
        )
        if explicit is not None:
            return explicit

        atr = cls._numeric_from_keys(values, ("atr", "average_true_range"))
        close = cls._numeric_from_keys(values, ("close", "close_price", "last_close"))
        if atr is None or close is None or close == 0:
            return None
        return atr / close

    def _direction_from_di(
        self,
        *,
        plus_di: Optional[float],
        minus_di: Optional[float],
        adx_score: float,
        has_adx_input: bool,
        adx_trend_threshold: Optional[float] = None,
    ) -> str:
        if not has_adx_input:
            return "unknown"
        threshold = (
            self.adx_trend_threshold
            if adx_trend_threshold is None
            else adx_trend_threshold
        )
        if adx_score < threshold:
            return "neutral"
        if plus_di is None or minus_di is None:
            return "unknown"
        if plus_di > minus_di:
            return "bullish"
        if minus_di > plus_di:
            return "bearish"
        return "neutral"

    @staticmethod
    def _signed_adx_score(
        *,
        adx_score: float,
        plus_di: Optional[float],
        minus_di: Optional[float],
        has_adx_input: bool,
    ) -> Optional[float]:
        if not has_adx_input:
            return None
        if plus_di is not None and minus_di is not None and minus_di > plus_di:
            return -adx_score
        return adx_score
