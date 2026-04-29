from typing import Mapping, Optional

from quant.schemas import FeatureSnapshot, RegimeKind, RegimeSnapshot


class RuleBasedRegimeDetector:
    def __init__(
        self,
        *,
        detector_id: str = "rule_based_regime",
        detector_version: str = "1.0.0",
        trend_threshold: float = 0.01,
        volatility_threshold: float = 0.03,
    ):
        if trend_threshold < 0:
            raise ValueError("trend_threshold must be >= 0")
        if volatility_threshold < 0:
            raise ValueError("volatility_threshold must be >= 0")

        self.detector_id = detector_id
        self.detector_version = detector_version
        self.trend_threshold = trend_threshold
        self.volatility_threshold = volatility_threshold

    def detect(self, snapshot: FeatureSnapshot) -> RegimeSnapshot:
        trend_score = self._trend_score(snapshot.values)
        volatility_score = self._volatility_score(snapshot.values)

        if volatility_score >= self.volatility_threshold:
            regime = RegimeKind.VOLATILE
            reason_codes = ["volatility_threshold_exceeded"]
            confidence = self._bounded_confidence(volatility_score, self.volatility_threshold)
        elif trend_score >= self.trend_threshold:
            regime = RegimeKind.TREND
            reason_codes = ["trend_threshold_exceeded"]
            confidence = self._bounded_confidence(trend_score, self.trend_threshold)
        else:
            regime = RegimeKind.RANGE
            reason_codes = ["no_trend_or_volatility_threshold"]
            confidence = 0.55

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
            metrics={
                "trend_score": trend_score,
                "trend_threshold": self.trend_threshold,
                "volatility_score": volatility_score,
                "volatility_threshold": self.volatility_threshold,
            },
            trace=snapshot.trace,
        )

    @staticmethod
    def _trend_score(values: Mapping[str, object]) -> float:
        explicit_score = RuleBasedRegimeDetector._numeric(values.get("trend_strength"))
        if explicit_score is not None:
            return abs(explicit_score)

        fast_ma = RuleBasedRegimeDetector._numeric(values.get("ma_fast"))
        slow_ma = RuleBasedRegimeDetector._numeric(values.get("ma_slow"))
        if fast_ma is None or slow_ma is None or slow_ma == 0:
            return 0.0
        return abs((fast_ma - slow_ma) / slow_ma)

    @staticmethod
    def _volatility_score(values: Mapping[str, object]) -> float:
        for key in ("volatility", "atr_pct"):
            value = RuleBasedRegimeDetector._numeric(values.get(key))
            if value is not None:
                return abs(value)
        return 0.0

    @staticmethod
    def _numeric(value: object) -> Optional[float]:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None

    @staticmethod
    def _bounded_confidence(score: float, threshold: float) -> float:
        if threshold == 0:
            return 1.0
        return max(0.5, min(0.95, score / (threshold * 2.0)))
