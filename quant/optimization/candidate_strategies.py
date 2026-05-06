from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

from quant.schemas import StrategySignal, TradeSide
from quant.strategy.base.strategy import Strategy
from quant.strategy.ma_crossover import MACrossoverStrategy


FeaturePipeline = Callable[[list[Any]], dict[str, list[Any]]]


CANDIDATE_REGIME_CONTEXT_CONTRACT_VERSION = "1.0"
CANDIDATE_STRATEGY_METADATA_CONTRACT_VERSION = "1.0"
CANDIDATE_REGIME_CONTEXT_FEATURES = (
    "candidate_market_regime",
    "candidate_regime_direction",
    "candidate_volatility_state",
    "candidate_regime_tradability",
    "candidate_regime_trend_score",
    "candidate_regime_volatility_score",
    "candidate_regime_liquidity_score",
    "candidate_regime_reason_codes",
)
_REGIME_FILTER_PARAMETER_NAMES = {
    "use_regime_filter",
    "allow_regime_fallback",
    "require_regime_tradable",
    "regime_direction_filter",
    "regime_volatility_filter",
}
_REGIME_DIRECTIONS = {"any", "bullish", "bearish", "neutral", "unknown"}
_REGIME_VOLATILITY_FILTERS = {
    "any",
    "low",
    "normal",
    "high",
    "extreme",
    "low_or_normal",
    "normal_or_high",
    "not_extreme",
}


SUPPORTED_CANDIDATE_STRATEGY_IDS = (
    "ma_crossover",
    "ema_trend_filter",
    "keltner_breakout",
    "volume_breakout",
    "macd_momentum",
    "roc_momentum",
    "stochastic_reversion",
    "ema_pullback_reentry",
    "atr_channel_reversion",
    "gap_reversal",
    "gap_continuation_breakout",
    "liquidity_sweep_reversal",
    "volatility_squeeze_breakout",
    "range_compression_breakout",
    "trend_pullback_breakout",
    "chandelier_breakout",
    "rolling_vwap_reversion",
    "donchian_breakout",
    "rsi_mean_reversion",
    "bollinger_reversion",
)


@dataclass(frozen=True)
class CandidateRegimeContext:
    market_regime: str
    direction: str
    volatility_state: str
    tradability: str
    trend_score: float
    volatility_score: float
    liquidity_score: float
    reason_codes: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "market_regime": self.market_regime,
            "direction": self.direction,
            "volatility_state": self.volatility_state,
            "tradability": self.tradability,
            "trend_score": self.trend_score,
            "volatility_score": self.volatility_score,
            "liquidity_score": self.liquidity_score,
            "reason_codes": list(self.reason_codes),
            "contract_version": CANDIDATE_REGIME_CONTEXT_CONTRACT_VERSION,
        }


@dataclass(frozen=True)
class CandidateStrategySpec:
    strategy_id: str
    strategy_version: str
    parameters: dict[str, Any]
    strategy: Strategy
    feature_pipeline: FeaturePipeline | None = None
    engine_fast_window: int | None = None
    engine_slow_window: int | None = None
    regime_context_contract: dict[str, Any] | None = None

    @property
    def strategy_metadata(self) -> dict[str, Any]:
        return candidate_strategy_metadata(self.strategy_id, self.parameters)


def candidate_regime_context_contract() -> dict[str, Any]:
    return {
        "contract_version": CANDIDATE_REGIME_CONTEXT_CONTRACT_VERSION,
        "feature_keys": list(CANDIDATE_REGIME_CONTEXT_FEATURES),
        "replay_contract": {
            "derived_from_ohlcv_only": True,
            "uses_no_future_bars": True,
            "missing_context_fallback_is_parameterized": True,
            "does_not_call_broker_risk_execution_or_portfolio": True,
        },
        "labels": {
            "market_regime": [
                "uptrend_low_vol",
                "uptrend_normal_vol",
                "uptrend_high_vol",
                "downtrend_low_vol",
                "downtrend_normal_vol",
                "downtrend_high_vol",
                "range_low_vol",
                "range_normal_vol",
                "range_high_vol",
                "chaos",
                "unknown",
            ],
            "direction": sorted(_REGIME_DIRECTIONS - {"any"}),
            "volatility_state": ["low", "normal", "high", "extreme", "unknown"],
            "tradability": ["tradable", "observe_only", "avoid"],
        },
        "reason_codes": [
            "candidate_regime_context:trend",
            "candidate_regime_context:range",
            "candidate_regime_context:chaos",
            "candidate_regime_context:unknown",
            "candidate_regime_filter_passed",
            "candidate_regime_context_missing_fallback",
        ],
    }


_REGIME_AWARE_CANDIDATE_STRATEGY_IDS = {
    "ema_trend_filter",
    "trend_pullback_breakout",
    "rolling_vwap_reversion",
}
_CANDIDATE_STRATEGY_METADATA_PROFILES: dict[str, dict[str, Any]] = {
    "ma_crossover": {
        "strategy_family": "trend_following",
        "applicable_regimes": ["uptrend_normal_vol", "uptrend_high_vol"],
        "feature_requirements": ["fast_ma", "slow_ma"],
        "entry_reason_codes": ["ma_crossover_buy"],
        "exit_reason_codes": ["ma_crossover_sell"],
    },
    "ema_trend_filter": {
        "strategy_family": "regime_aware_trend_filter",
        "applicable_regimes": ["uptrend_low_vol", "uptrend_normal_vol", "uptrend_high_vol"],
        "feature_requirements": [
            "fast_ema",
            "slow_ema",
            "atr_pct",
            "return_volatility_pct",
            "trend_strength",
        ],
        "entry_reason_codes": ["ema_trend_cross_up", "atr_volatility_filter_passed"],
        "exit_reason_codes": ["ema_trend_cross_down", "atr_volatility_filter_passed"],
    },
    "donchian_breakout": {
        "strategy_family": "breakout",
        "applicable_regimes": ["uptrend_normal_vol", "uptrend_high_vol"],
        "feature_requirements": ["close", "donchian_high", "exit_low"],
        "entry_reason_codes": ["donchian_breakout_up"],
        "exit_reason_codes": ["donchian_exit_down"],
    },
    "keltner_breakout": {
        "strategy_family": "breakout",
        "applicable_regimes": ["uptrend_normal_vol", "uptrend_high_vol"],
        "feature_requirements": ["close", "keltner_mid", "keltner_upper", "atr"],
        "entry_reason_codes": ["keltner_upper_breakout"],
        "exit_reason_codes": ["keltner_midline_exit"],
    },
    "volume_breakout": {
        "strategy_family": "volume_confirmed_breakout",
        "applicable_regimes": ["uptrend_normal_vol", "uptrend_high_vol", "range_normal_vol"],
        "feature_requirements": ["close", "breakout_high", "exit_low", "volume_ratio"],
        "entry_reason_codes": ["volume_confirmed_price_breakout"],
        "exit_reason_codes": ["volume_breakout_exit_down"],
    },
    "macd_momentum": {
        "strategy_family": "momentum",
        "applicable_regimes": ["uptrend_normal_vol", "uptrend_high_vol"],
        "feature_requirements": ["macd_line", "macd_signal", "macd_histogram_pct", "atr_pct"],
        "entry_reason_codes": ["macd_bullish_momentum_cross"],
        "exit_reason_codes": ["macd_bearish_signal_exit"],
    },
    "roc_momentum": {
        "strategy_family": "momentum",
        "applicable_regimes": ["uptrend_normal_vol", "uptrend_high_vol"],
        "feature_requirements": ["roc_pct", "trend_ema", "atr_pct"],
        "entry_reason_codes": ["roc_momentum_breakout", "trend_filter_passed"],
        "exit_reason_codes": ["roc_momentum_exit"],
    },
    "stochastic_reversion": {
        "strategy_family": "mean_reversion",
        "applicable_regimes": ["range_low_vol", "range_normal_vol"],
        "feature_requirements": ["stochastic_k", "stochastic_d"],
        "entry_reason_codes": ["stochastic_oversold_rebound"],
        "exit_reason_codes": ["stochastic_reversion_exit"],
    },
    "ema_pullback_reentry": {
        "strategy_family": "trend_pullback",
        "applicable_regimes": ["uptrend_low_vol", "uptrend_normal_vol"],
        "feature_requirements": ["close", "fast_ema", "slow_ema", "rsi", "atr_pct"],
        "entry_reason_codes": ["ema_pullback_reentry", "trend_filter_passed"],
        "exit_reason_codes": ["ema_pullback_reentry_exit"],
    },
    "atr_channel_reversion": {
        "strategy_family": "volatility_normalized_reversion",
        "applicable_regimes": ["range_normal_vol", "range_high_vol"],
        "feature_requirements": ["close", "channel_mid", "channel_lower", "channel_upper", "atr_pct"],
        "entry_reason_codes": ["atr_channel_lower_reversion", "atr_volatility_filter_passed"],
        "exit_reason_codes": ["atr_channel_reversion_exit"],
    },
    "gap_reversal": {
        "strategy_family": "gap_reversion",
        "applicable_regimes": ["range_normal_vol", "range_high_vol"],
        "feature_requirements": ["gap_pct", "gap_reclaim_ratio", "volume_ratio", "atr_pct"],
        "entry_reason_codes": ["down_gap_reversal", "volume_filter_passed", "atr_volatility_filter_passed"],
        "exit_reason_codes": ["up_gap_reversal_exit"],
    },
    "gap_continuation_breakout": {
        "strategy_family": "gap_continuation",
        "applicable_regimes": ["uptrend_normal_vol", "uptrend_high_vol"],
        "feature_requirements": ["gap_pct", "gap_follow_through_ratio", "volume_ratio", "atr_pct"],
        "entry_reason_codes": [
            "up_gap_continuation_breakout",
            "follow_through_filter_passed",
            "volume_filter_passed",
            "atr_volatility_filter_passed",
        ],
        "exit_reason_codes": ["down_gap_continuation_exit"],
    },
    "liquidity_sweep_reversal": {
        "strategy_family": "liquidity_reversion",
        "applicable_regimes": ["range_normal_vol", "range_high_vol"],
        "feature_requirements": ["range_low", "range_high", "close_position", "volume_ratio", "atr_pct"],
        "entry_reason_codes": ["liquidity_sweep_low_reversal", "volume_filter_passed", "atr_volatility_filter_passed"],
        "exit_reason_codes": ["liquidity_sweep_high_reversal_exit"],
    },
    "volatility_squeeze_breakout": {
        "strategy_family": "low_vol_breakout",
        "applicable_regimes": ["range_low_vol", "uptrend_low_vol"],
        "feature_requirements": ["breakout_high", "squeeze_ratio", "volume_ratio", "atr_pct", "squeeze_mid"],
        "entry_reason_codes": ["volatility_squeeze_breakout", "volume_filter_passed"],
        "exit_reason_codes": ["volatility_squeeze_midline_exit"],
    },
    "range_compression_breakout": {
        "strategy_family": "range_breakout",
        "applicable_regimes": ["range_low_vol", "range_normal_vol"],
        "feature_requirements": [
            "range_high",
            "range_mid",
            "range_width_pct",
            "range_compression_ratio",
            "volume_ratio",
            "atr_pct",
        ],
        "entry_reason_codes": ["range_compression_breakout", "volume_filter_passed", "atr_volatility_filter_passed"],
        "exit_reason_codes": ["range_compression_midline_exit"],
    },
    "trend_pullback_breakout": {
        "strategy_family": "regime_aware_trend_pullback",
        "applicable_regimes": ["uptrend_low_vol", "uptrend_normal_vol", "uptrend_high_vol"],
        "feature_requirements": [
            "fast_ema",
            "slow_ema",
            "breakout_high",
            "pullback_depth_pct",
            "trend_spread_pct",
            "volume_ratio",
            "atr_pct",
        ],
        "entry_reason_codes": [
            "trend_pullback_breakout",
            "pullback_filter_passed",
            "volume_filter_passed",
            "atr_volatility_filter_passed",
        ],
        "exit_reason_codes": ["trend_pullback_fast_ema_loss_exit"],
    },
    "chandelier_breakout": {
        "strategy_family": "trend_breakout_trailing_stop",
        "applicable_regimes": ["uptrend_normal_vol", "uptrend_high_vol"],
        "feature_requirements": ["breakout_high", "chandelier_stop", "volume_ratio", "atr_pct"],
        "entry_reason_codes": ["chandelier_breakout", "volume_filter_passed", "atr_volatility_filter_passed"],
        "exit_reason_codes": ["chandelier_trailing_stop_exit"],
    },
    "rolling_vwap_reversion": {
        "strategy_family": "regime_aware_vwap_reversion",
        "applicable_regimes": ["range_low_vol", "range_normal_vol"],
        "feature_requirements": ["rolling_vwap", "lower_band", "volume_ratio", "atr_pct"],
        "entry_reason_codes": [
            "rolling_vwap_lower_band_reversion_entry",
            "volume_filter_passed",
            "atr_volatility_filter_passed",
        ],
        "exit_reason_codes": ["rolling_vwap_reclaim_exit"],
    },
    "rsi_mean_reversion": {
        "strategy_family": "mean_reversion",
        "applicable_regimes": ["range_low_vol", "range_normal_vol"],
        "feature_requirements": ["rsi"],
        "entry_reason_codes": ["rsi_oversold_rebound"],
        "exit_reason_codes": ["rsi_mean_reversion_exit"],
    },
    "bollinger_reversion": {
        "strategy_family": "mean_reversion",
        "applicable_regimes": ["range_normal_vol", "range_high_vol"],
        "feature_requirements": ["bollinger_mid", "bollinger_lower", "bollinger_upper"],
        "entry_reason_codes": ["bollinger_lower_reversion_entry"],
        "exit_reason_codes": ["bollinger_midline_exit"],
    },
}


def candidate_strategy_metadata(
    strategy_id: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_id = _normalize_strategy_id(strategy_id)
    normalized_parameters = normalize_candidate_strategy_parameters(
        normalized_id,
        parameters or {},
    )
    profile = _CANDIDATE_STRATEGY_METADATA_PROFILES[normalized_id]
    regime_aware = normalized_id in _REGIME_AWARE_CANDIDATE_STRATEGY_IDS
    metadata = {
        "metadata_contract_version": CANDIDATE_STRATEGY_METADATA_CONTRACT_VERSION,
        "strategy_id": normalized_id,
        "strategy_version": "1.0",
        "strategy_family": profile["strategy_family"],
        "cross_symbol": True,
        "symbol_agnostic_ohlcv_input": True,
        "deterministic": True,
        "stateless_delayed_signal": True,
        "does_not_call_broker_risk_execution_or_portfolio": True,
        "does_not_send_orders": True,
        "does_not_modify_live_state": True,
        "feature_requirements": ["ohlcv"] + list(profile["feature_requirements"]),
        "min_bars": _candidate_strategy_min_bars(normalized_parameters),
        "parameter_space": {
            "grid_enumerable": True,
            "parameter_keys": list(normalized_parameters),
            "selected_parameters": dict(normalized_parameters),
        },
        "applicable_regimes": list(profile["applicable_regimes"]),
        "regime_aware": regime_aware,
        "regime_context_contract_version": (
            CANDIDATE_REGIME_CONTEXT_CONTRACT_VERSION if regime_aware else None
        ),
        "reason_codes": {
            "entry": list(profile["entry_reason_codes"]),
            "exit": list(profile["exit_reason_codes"]),
            "metadata": [
                "candidate_strategy_metadata_v1",
                "cross_symbol_ohlcv_contract",
            ],
        },
    }
    if regime_aware:
        metadata["feature_requirements"].extend(CANDIDATE_REGIME_CONTEXT_FEATURES)
        metadata["reason_codes"]["metadata"].append("regime_context_contract_supported")
        metadata["regime_context_contract"] = candidate_regime_context_contract()
    return metadata


class _DelayedSignalStrategy(Strategy):
    def on_bar(self, features, index):
        if index <= 0:
            return []

        signal = self.generate_signal(features, index - 1)
        if signal is None:
            return []

        return [signal.with_execute_index(index)]

    def _build_signal(self, side: TradeSide, index: int, reason_codes: list[str]):
        return StrategySignal(
            signal_id=f"{self.strategy_id}:{index}:{side.value}",
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            side=side,
            signal_index=index,
            reason_codes=reason_codes,
        )


class EMATrendFilterStrategy(_DelayedSignalStrategy):
    def __init__(self, strategy_id: str, strategy_version: str, parameters: dict[str, Any]):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.parameters = dict(parameters)

    def generate_signal(self, features, index):
        if index <= 0:
            return None

        required = (
            features["fast_ema"][index - 1],
            features["slow_ema"][index - 1],
            features["fast_ema"][index],
            features["slow_ema"][index],
            features["atr_pct"][index],
            features["return_volatility_pct"][index],
            features["trend_strength"][index],
        )
        if any(value is None for value in required):
            return None

        if not self._filters_pass(features, index):
            return None

        previous_fast, previous_slow, current_fast, current_slow = required[:4]
        if previous_fast <= previous_slow and current_fast > current_slow:
            return self._build_signal(
                TradeSide.BUY,
                index,
                ["ema_trend_cross_up", "atr_volatility_filter_passed"],
            )
        if previous_fast >= previous_slow and current_fast < current_slow:
            return self._build_signal(
                TradeSide.SELL,
                index,
                ["ema_trend_cross_down", "atr_volatility_filter_passed"],
            )
        return None

    def _filters_pass(self, features, index):
        atr_pct = float(features["atr_pct"][index])
        volatility_pct = float(features["return_volatility_pct"][index])
        trend_strength = float(features["trend_strength"][index])
        return (
            _regime_filter_passes(features, index, self.parameters)
            and
            atr_pct <= self.parameters["max_atr_pct"]
            and volatility_pct <= self.parameters["max_volatility_pct"]
            and trend_strength >= self.parameters["min_trend_strength"]
        )


class DonchianBreakoutStrategy(_DelayedSignalStrategy):
    def __init__(self, strategy_id: str, strategy_version: str, parameters: dict[str, Any]):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.parameters = dict(parameters)

    def generate_signal(self, features, index):
        if index <= 0:
            return None

        values = (
            features["close"][index - 1],
            features["close"][index],
            features["donchian_high"][index - 1],
            features["donchian_high"][index],
            features["exit_low"][index - 1],
            features["exit_low"][index],
        )
        if any(value is None for value in values):
            return None

        previous_close, current_close, previous_high, current_high, previous_exit_low, current_exit_low = values
        if previous_close <= previous_high and current_close > current_high:
            return self._build_signal(
                TradeSide.BUY,
                index,
                ["donchian_breakout_up"],
            )
        if previous_close >= previous_exit_low and current_close < current_exit_low:
            return self._build_signal(
                TradeSide.SELL,
                index,
                ["donchian_exit_down"],
            )
        return None


class KeltnerBreakoutStrategy(_DelayedSignalStrategy):
    def __init__(self, strategy_id: str, strategy_version: str, parameters: dict[str, Any]):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.parameters = dict(parameters)

    def generate_signal(self, features, index):
        if index <= 0:
            return None

        values = (
            features["close"][index - 1],
            features["close"][index],
            features["keltner_upper"][index - 1],
            features["keltner_upper"][index],
            features["keltner_mid"][index - 1],
            features["keltner_mid"][index],
        )
        if any(value is None for value in values):
            return None

        previous_close, current_close, previous_upper, current_upper, previous_mid, current_mid = values
        if previous_close <= previous_upper and current_close > current_upper:
            return self._build_signal(
                TradeSide.BUY,
                index,
                ["keltner_upper_breakout"],
            )
        if (
            self.parameters["exit_midline"]
            and previous_close >= previous_mid
            and current_close < current_mid
        ):
            return self._build_signal(
                TradeSide.SELL,
                index,
                ["keltner_midline_exit"],
            )
        return None


class VolumeBreakoutStrategy(_DelayedSignalStrategy):
    def __init__(self, strategy_id: str, strategy_version: str, parameters: dict[str, Any]):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.parameters = dict(parameters)

    def generate_signal(self, features, index):
        if index <= 0:
            return None

        values = (
            features["close"][index - 1],
            features["close"][index],
            features["breakout_high"][index - 1],
            features["breakout_high"][index],
            features["exit_low"][index - 1],
            features["exit_low"][index],
            features["volume_ratio"][index],
        )
        if any(value is None for value in values):
            return None

        (
            previous_close,
            current_close,
            previous_breakout_high,
            current_breakout_high,
            previous_exit_low,
            current_exit_low,
            current_volume_ratio,
        ) = values
        if (
            previous_close <= previous_breakout_high
            and current_close > current_breakout_high
            and current_volume_ratio >= self.parameters["min_volume_ratio"]
        ):
            return self._build_signal(
                TradeSide.BUY,
                index,
                ["volume_confirmed_price_breakout"],
            )
        if (
            self.parameters["exit_on_breakdown"]
            and previous_close >= previous_exit_low
            and current_close < current_exit_low
        ):
            return self._build_signal(
                TradeSide.SELL,
                index,
                ["volume_breakout_exit_down"],
            )
        return None


class MACDMomentumStrategy(_DelayedSignalStrategy):
    def __init__(self, strategy_id: str, strategy_version: str, parameters: dict[str, Any]):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.parameters = dict(parameters)

    def generate_signal(self, features, index):
        if index <= 0:
            return None

        values = (
            features["macd_line"][index - 1],
            features["macd_line"][index],
            features["macd_signal"][index - 1],
            features["macd_signal"][index],
            features["macd_histogram_pct"][index],
            features["atr_pct"][index],
        )
        if any(value is None for value in values):
            return None

        (
            previous_macd,
            current_macd,
            previous_signal,
            current_signal,
            current_histogram_pct,
            current_atr_pct,
        ) = values
        if (
            previous_macd <= previous_signal
            and current_macd > current_signal
            and current_histogram_pct >= self.parameters["min_histogram_pct"]
            and current_atr_pct <= self.parameters["max_atr_pct"]
        ):
            return self._build_signal(
                TradeSide.BUY,
                index,
                ["macd_bullish_momentum_cross"],
            )
        if (
            self.parameters["exit_on_signal_cross"]
            and previous_macd >= previous_signal
            and current_macd < current_signal
        ):
            return self._build_signal(
                TradeSide.SELL,
                index,
                ["macd_bearish_signal_exit"],
            )
        return None


class ROCMomentumStrategy(_DelayedSignalStrategy):
    def __init__(self, strategy_id: str, strategy_version: str, parameters: dict[str, Any]):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.parameters = dict(parameters)

    def generate_signal(self, features, index):
        if index <= 0:
            return None

        values = (
            features["roc_pct"][index - 1],
            features["roc_pct"][index],
            features["close"][index],
            features["trend_ema"][index],
            features["atr_pct"][index],
        )
        if any(value is None for value in values):
            return None

        previous_roc_pct, current_roc_pct, current_close, current_trend_ema, current_atr_pct = values
        if (
            previous_roc_pct <= self.parameters["min_roc_pct"]
            and current_roc_pct > self.parameters["min_roc_pct"]
            and current_close >= current_trend_ema
            and current_atr_pct <= self.parameters["max_atr_pct"]
        ):
            return self._build_signal(
                TradeSide.BUY,
                index,
                ["roc_momentum_breakout", "trend_filter_passed"],
            )
        if (
            self.parameters["exit_on_trend_loss"]
            and (
                current_roc_pct <= self.parameters["exit_roc_pct"]
                or current_close < current_trend_ema
            )
        ):
            return self._build_signal(
                TradeSide.SELL,
                index,
                ["roc_momentum_exit"],
            )
        return None


class StochasticReversionStrategy(_DelayedSignalStrategy):
    def __init__(self, strategy_id: str, strategy_version: str, parameters: dict[str, Any]):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.parameters = dict(parameters)

    def generate_signal(self, features, index):
        if index <= 0:
            return None

        values = (
            features["stochastic_k"][index - 1],
            features["stochastic_k"][index],
            features["stochastic_d"][index],
        )
        if any(value is None for value in values):
            return None

        previous_k, current_k, current_d = values
        if (
            previous_k <= self.parameters["oversold"]
            and current_k > self.parameters["oversold"]
            and current_k >= current_d
        ):
            return self._build_signal(
                TradeSide.BUY,
                index,
                ["stochastic_oversold_rebound"],
            )
        if (
            self.parameters["exit_on_midline"]
            and previous_k < self.parameters["exit_k"] <= current_k
        ) or current_k >= self.parameters["overbought"]:
            return self._build_signal(
                TradeSide.SELL,
                index,
                ["stochastic_reversion_exit"],
            )
        return None


class EMAPullbackReentryStrategy(_DelayedSignalStrategy):
    def __init__(self, strategy_id: str, strategy_version: str, parameters: dict[str, Any]):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.parameters = dict(parameters)

    def generate_signal(self, features, index):
        if index <= 0:
            return None

        values = (
            features["close"][index - 1],
            features["close"][index],
            features["fast_ema"][index - 1],
            features["fast_ema"][index],
            features["slow_ema"][index],
            features["rsi"][index - 1],
            features["rsi"][index],
            features["atr_pct"][index],
        )
        if any(value is None for value in values):
            return None

        (
            previous_close,
            current_close,
            previous_fast_ema,
            current_fast_ema,
            current_slow_ema,
            previous_rsi,
            current_rsi,
            current_atr_pct,
        ) = values
        trend_filter_passed = current_fast_ema > current_slow_ema
        pulled_back = (
            previous_close <= previous_fast_ema
            or previous_rsi <= self.parameters["pullback_rsi"]
        )
        reentered = (
            current_close > current_fast_ema
            and current_rsi >= self.parameters["reentry_rsi"]
        )
        if (
            trend_filter_passed
            and pulled_back
            and reentered
            and current_atr_pct <= self.parameters["max_atr_pct"]
        ):
            return self._build_signal(
                TradeSide.BUY,
                index,
                ["ema_pullback_reentry", "trend_filter_passed"],
            )
        if (
            self.parameters["exit_on_trend_loss"]
            and (
                current_close < current_slow_ema
                or current_rsi >= self.parameters["exit_rsi"]
            )
        ):
            return self._build_signal(
                TradeSide.SELL,
                index,
                ["ema_pullback_reentry_exit"],
            )
        return None


class ATRChannelReversionStrategy(_DelayedSignalStrategy):
    def __init__(self, strategy_id: str, strategy_version: str, parameters: dict[str, Any]):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.parameters = dict(parameters)

    def generate_signal(self, features, index):
        if index <= 0:
            return None

        values = (
            features["close"][index - 1],
            features["close"][index],
            features["channel_lower"][index - 1],
            features["channel_lower"][index],
            features["channel_mid"][index - 1],
            features["channel_mid"][index],
            features["channel_upper"][index],
            features["atr_pct"][index],
        )
        if any(value is None for value in values):
            return None

        (
            previous_close,
            current_close,
            previous_lower,
            current_lower,
            previous_mid,
            current_mid,
            current_upper,
            current_atr_pct,
        ) = values
        volatility_filter_passed = (
            self.parameters["min_atr_pct"]
            <= current_atr_pct
            <= self.parameters["max_atr_pct"]
        )
        if (
            volatility_filter_passed
            and previous_close <= previous_lower
            and current_close > current_lower
        ):
            return self._build_signal(
                TradeSide.BUY,
                index,
                ["atr_channel_lower_reversion", "atr_volatility_filter_passed"],
            )
        if (
            self.parameters["exit_midline"]
            and previous_close <= previous_mid
            and current_close > current_mid
        ) or current_close >= current_upper:
            return self._build_signal(
                TradeSide.SELL,
                index,
                ["atr_channel_reversion_exit"],
            )
        return None


class GapReversalStrategy(_DelayedSignalStrategy):
    def __init__(self, strategy_id: str, strategy_version: str, parameters: dict[str, Any]):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.parameters = dict(parameters)

    def generate_signal(self, features, index):
        if index <= 0:
            return None

        values = (
            features["gap_pct"][index],
            features["gap_reclaim_ratio"][index],
            features["volume_ratio"][index],
            features["atr_pct"][index],
        )
        if any(value is None for value in values):
            return None

        gap_pct, gap_reclaim_ratio, volume_ratio, atr_pct = values
        volatility_filter_passed = atr_pct <= self.parameters["max_atr_pct"]
        volume_filter_passed = volume_ratio >= self.parameters["min_volume_ratio"]
        if (
            gap_pct <= -self.parameters["min_gap_pct"]
            and gap_reclaim_ratio >= self.parameters["min_reclaim_ratio"]
            and volatility_filter_passed
            and volume_filter_passed
        ):
            return self._build_signal(
                TradeSide.BUY,
                index,
                ["down_gap_reversal", "volume_filter_passed", "atr_volatility_filter_passed"],
            )
        if (
            self.parameters["exit_on_up_gap"]
            and gap_pct >= self.parameters["min_gap_pct"]
            and gap_reclaim_ratio <= -self.parameters["min_reclaim_ratio"]
        ):
            return self._build_signal(
                TradeSide.SELL,
                index,
                ["up_gap_reversal_exit"],
            )
        return None


class GapContinuationBreakoutStrategy(_DelayedSignalStrategy):
    def __init__(self, strategy_id: str, strategy_version: str, parameters: dict[str, Any]):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.parameters = dict(parameters)

    def generate_signal(self, features, index):
        if index <= 0:
            return None

        values = (
            features["gap_pct"][index],
            features["gap_follow_through_ratio"][index],
            features["volume_ratio"][index],
            features["atr_pct"][index],
        )
        if any(value is None for value in values):
            return None

        gap_pct, follow_through_ratio, volume_ratio, atr_pct = values
        volatility_filter_passed = (
            self.parameters["min_atr_pct"]
            <= atr_pct
            <= self.parameters["max_atr_pct"]
        )
        volume_filter_passed = volume_ratio >= self.parameters["min_volume_ratio"]
        if (
            gap_pct >= self.parameters["min_gap_pct"]
            and follow_through_ratio >= self.parameters["min_follow_through_ratio"]
            and volatility_filter_passed
            and volume_filter_passed
        ):
            return self._build_signal(
                TradeSide.BUY,
                index,
                [
                    "up_gap_continuation_breakout",
                    "follow_through_filter_passed",
                    "volume_filter_passed",
                    "atr_volatility_filter_passed",
                ],
            )
        if (
            self.parameters["exit_on_down_gap"]
            and gap_pct <= -self.parameters["min_gap_pct"]
            and follow_through_ratio <= -self.parameters["min_follow_through_ratio"]
        ):
            return self._build_signal(
                TradeSide.SELL,
                index,
                ["down_gap_continuation_exit"],
            )
        return None


class LiquiditySweepReversalStrategy(_DelayedSignalStrategy):
    def __init__(self, strategy_id: str, strategy_version: str, parameters: dict[str, Any]):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.parameters = dict(parameters)

    def generate_signal(self, features, index):
        if index <= 0:
            return None

        values = (
            features["low"][index],
            features["high"][index],
            features["close"][index],
            features["range_low"][index],
            features["range_high"][index],
            features["close_position"][index],
            features["volume_ratio"][index],
            features["atr_pct"][index],
        )
        if any(value is None for value in values):
            return None

        (
            current_low,
            current_high,
            current_close,
            previous_range_low,
            previous_range_high,
            close_position,
            volume_ratio,
            atr_pct,
        ) = values
        volatility_filter_passed = (
            self.parameters["min_atr_pct"]
            <= atr_pct
            <= self.parameters["max_atr_pct"]
        )
        volume_filter_passed = volume_ratio >= self.parameters["min_volume_ratio"]
        min_sweep_pct = self.parameters["min_sweep_pct"]
        if (
            previous_range_low > 0.0
            and current_low <= previous_range_low * (1.0 - min_sweep_pct)
            and current_close > previous_range_low
            and close_position >= self.parameters["min_close_position"]
            and volatility_filter_passed
            and volume_filter_passed
        ):
            return self._build_signal(
                TradeSide.BUY,
                index,
                [
                    "liquidity_sweep_low_reversal",
                    "volume_filter_passed",
                    "atr_volatility_filter_passed",
                ],
            )
        if (
            self.parameters["exit_on_bearish_sweep"]
            and previous_range_high > 0.0
            and current_high >= previous_range_high * (1.0 + min_sweep_pct)
            and current_close < previous_range_high
            and close_position <= 1.0 - self.parameters["min_close_position"]
        ):
            return self._build_signal(
                TradeSide.SELL,
                index,
                ["liquidity_sweep_high_reversal_exit"],
            )
        return None


class VolatilitySqueezeBreakoutStrategy(_DelayedSignalStrategy):
    def __init__(self, strategy_id: str, strategy_version: str, parameters: dict[str, Any]):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.parameters = dict(parameters)

    def generate_signal(self, features, index):
        if index <= 0:
            return None

        values = (
            features["close"][index - 1],
            features["close"][index],
            features["breakout_high"][index - 1],
            features["breakout_high"][index],
            features["squeeze_ratio"][index],
            features["volume_ratio"][index],
            features["atr_pct"][index],
            features["squeeze_mid"][index - 1],
            features["squeeze_mid"][index],
        )
        if any(value is None for value in values):
            return None

        (
            previous_close,
            current_close,
            previous_breakout_high,
            current_breakout_high,
            squeeze_ratio,
            volume_ratio,
            atr_pct,
            previous_mid,
            current_mid,
        ) = values
        volatility_filter_passed = (
            self.parameters["min_atr_pct"]
            <= atr_pct
            <= self.parameters["max_atr_pct"]
        )
        if (
            previous_close <= previous_breakout_high
            and current_close > current_breakout_high
            and squeeze_ratio <= self.parameters["max_squeeze_ratio"]
            and volume_ratio >= self.parameters["min_volume_ratio"]
            and volatility_filter_passed
        ):
            return self._build_signal(
                TradeSide.BUY,
                index,
                ["volatility_squeeze_breakout", "volume_filter_passed"],
            )
        if (
            self.parameters["exit_on_midline_loss"]
            and previous_close >= previous_mid
            and current_close < current_mid
        ):
            return self._build_signal(
                TradeSide.SELL,
                index,
                ["volatility_squeeze_midline_exit"],
            )
        return None


class RangeCompressionBreakoutStrategy(_DelayedSignalStrategy):
    def __init__(self, strategy_id: str, strategy_version: str, parameters: dict[str, Any]):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.parameters = dict(parameters)

    def generate_signal(self, features, index):
        if index <= 0:
            return None

        values = (
            features["close"][index - 1],
            features["close"][index],
            features["range_high"][index - 1],
            features["range_high"][index],
            features["range_mid"][index - 1],
            features["range_mid"][index],
            features["range_width_pct"][index],
            features["range_compression_ratio"][index],
            features["volume_ratio"][index],
            features["atr_pct"][index],
        )
        if any(value is None for value in values):
            return None

        (
            previous_close,
            current_close,
            previous_range_high,
            current_range_high,
            previous_range_mid,
            current_range_mid,
            range_width_pct,
            range_compression_ratio,
            volume_ratio,
            atr_pct,
        ) = values
        volatility_filter_passed = (
            self.parameters["min_atr_pct"]
            <= atr_pct
            <= self.parameters["max_atr_pct"]
        )
        compression_filter_passed = (
            range_width_pct <= self.parameters["max_range_width_pct"]
            and range_compression_ratio <= self.parameters["max_compression_ratio"]
        )
        if (
            previous_close <= previous_range_high
            and current_close > current_range_high
            and compression_filter_passed
            and volume_ratio >= self.parameters["min_volume_ratio"]
            and volatility_filter_passed
        ):
            return self._build_signal(
                TradeSide.BUY,
                index,
                [
                    "range_compression_breakout",
                    "volume_filter_passed",
                    "atr_volatility_filter_passed",
                ],
            )
        if (
            self.parameters["exit_on_midline_loss"]
            and previous_close >= previous_range_mid
            and current_close < current_range_mid
        ):
            return self._build_signal(
                TradeSide.SELL,
                index,
                ["range_compression_midline_exit"],
            )
        return None


class TrendPullbackBreakoutStrategy(_DelayedSignalStrategy):
    def __init__(self, strategy_id: str, strategy_version: str, parameters: dict[str, Any]):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.parameters = dict(parameters)

    def generate_signal(self, features, index):
        if index <= 0:
            return None

        values = (
            features["close"][index - 1],
            features["close"][index],
            features["fast_ema"][index - 1],
            features["fast_ema"][index],
            features["slow_ema"][index],
            features["breakout_high"][index - 1],
            features["breakout_high"][index],
            features["pullback_low"][index],
            features["pullback_depth_pct"][index],
            features["trend_spread_pct"][index],
            features["volume_ratio"][index],
            features["atr_pct"][index],
        )
        if any(value is None for value in values):
            return None

        (
            previous_close,
            current_close,
            previous_fast_ema,
            current_fast_ema,
            current_slow_ema,
            previous_breakout_high,
            current_breakout_high,
            _pullback_low,
            pullback_depth_pct,
            trend_spread_pct,
            volume_ratio,
            atr_pct,
        ) = values
        trend_filter_passed = (
            current_fast_ema > current_slow_ema
            and trend_spread_pct >= self.parameters["min_trend_spread_pct"]
        )
        pullback_filter_passed = (
            self.parameters["min_pullback_depth_pct"]
            <= pullback_depth_pct
            <= self.parameters["max_pullback_depth_pct"]
        )
        volatility_filter_passed = (
            self.parameters["min_atr_pct"]
            <= atr_pct
            <= self.parameters["max_atr_pct"]
        )
        if (
            _regime_filter_passes(features, index, self.parameters)
            and
            previous_close <= previous_breakout_high
            and current_close > current_breakout_high
            and trend_filter_passed
            and pullback_filter_passed
            and volume_ratio >= self.parameters["min_volume_ratio"]
            and volatility_filter_passed
        ):
            return self._build_signal(
                TradeSide.BUY,
                index,
                [
                    "trend_pullback_breakout",
                    "pullback_filter_passed",
                    "volume_filter_passed",
                    "atr_volatility_filter_passed",
                ],
            )
        if (
            self.parameters["exit_on_fast_ema_loss"]
            and previous_close >= previous_fast_ema
            and current_close < current_fast_ema
        ):
            return self._build_signal(
                TradeSide.SELL,
                index,
                ["trend_pullback_fast_ema_loss_exit"],
            )
        return None


class ChandelierBreakoutStrategy(_DelayedSignalStrategy):
    def __init__(self, strategy_id: str, strategy_version: str, parameters: dict[str, Any]):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.parameters = dict(parameters)

    def generate_signal(self, features, index):
        if index <= 0:
            return None

        values = (
            features["close"][index - 1],
            features["close"][index],
            features["breakout_high"][index - 1],
            features["breakout_high"][index],
            features["chandelier_stop"][index - 1],
            features["chandelier_stop"][index],
            features["volume_ratio"][index],
            features["atr_pct"][index],
        )
        if any(value is None for value in values):
            return None

        (
            previous_close,
            current_close,
            previous_breakout_high,
            current_breakout_high,
            previous_chandelier_stop,
            current_chandelier_stop,
            volume_ratio,
            atr_pct,
        ) = values
        volatility_filter_passed = (
            self.parameters["min_atr_pct"]
            <= atr_pct
            <= self.parameters["max_atr_pct"]
        )
        if (
            previous_close <= previous_breakout_high
            and current_close > current_breakout_high
            and volume_ratio >= self.parameters["min_volume_ratio"]
            and volatility_filter_passed
        ):
            return self._build_signal(
                TradeSide.BUY,
                index,
                [
                    "chandelier_breakout",
                    "volume_filter_passed",
                    "atr_volatility_filter_passed",
                ],
            )
        if (
            self.parameters["exit_on_chandelier_loss"]
            and previous_close >= previous_chandelier_stop
            and current_close < current_chandelier_stop
        ):
            return self._build_signal(
                TradeSide.SELL,
                index,
                ["chandelier_trailing_stop_exit"],
            )
        return None


class RollingVWAPReversionStrategy(_DelayedSignalStrategy):
    def __init__(self, strategy_id: str, strategy_version: str, parameters: dict[str, Any]):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.parameters = dict(parameters)

    def generate_signal(self, features, index):
        if index <= 0:
            return None

        values = (
            features["close"][index - 1],
            features["close"][index],
            features["rolling_vwap"][index - 1],
            features["rolling_vwap"][index],
            features["lower_band"][index - 1],
            features["lower_band"][index],
            features["volume_ratio"][index],
            features["atr_pct"][index],
        )
        if any(value is None for value in values):
            return None

        (
            previous_close,
            current_close,
            previous_vwap,
            current_vwap,
            previous_lower_band,
            current_lower_band,
            volume_ratio,
            atr_pct,
        ) = values
        volatility_filter_passed = (
            self.parameters["min_atr_pct"]
            <= atr_pct
            <= self.parameters["max_atr_pct"]
        )
        if (
            _regime_filter_passes(features, index, self.parameters)
            and
            previous_close >= previous_lower_band
            and current_close < current_lower_band
            and volume_ratio >= self.parameters["min_volume_ratio"]
            and volatility_filter_passed
        ):
            return self._build_signal(
                TradeSide.BUY,
                index,
                [
                    "rolling_vwap_lower_band_reversion_entry",
                    "volume_filter_passed",
                    "atr_volatility_filter_passed",
                ],
            )
        if (
            self.parameters["exit_on_vwap_reclaim"]
            and previous_close <= previous_vwap
            and current_close > current_vwap
        ):
            return self._build_signal(
                TradeSide.SELL,
                index,
                ["rolling_vwap_reclaim_exit"],
            )
        return None


class RSIMeanReversionStrategy(_DelayedSignalStrategy):
    def __init__(self, strategy_id: str, strategy_version: str, parameters: dict[str, Any]):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.parameters = dict(parameters)

    def generate_signal(self, features, index):
        if index <= 0:
            return None

        previous_rsi = features["rsi"][index - 1]
        current_rsi = features["rsi"][index]
        if previous_rsi is None or current_rsi is None:
            return None

        if previous_rsi < self.parameters["oversold"] <= current_rsi:
            return self._build_signal(
                TradeSide.BUY,
                index,
                ["rsi_oversold_rebound"],
            )
        if (
            previous_rsi < self.parameters["exit_rsi"] <= current_rsi
            or current_rsi >= self.parameters["overbought"]
        ):
            return self._build_signal(
                TradeSide.SELL,
                index,
                ["rsi_mean_reversion_exit"],
            )
        return None


class BollingerReversionStrategy(_DelayedSignalStrategy):
    def __init__(self, strategy_id: str, strategy_version: str, parameters: dict[str, Any]):
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.parameters = dict(parameters)

    def generate_signal(self, features, index):
        if index <= 0:
            return None

        values = (
            features["close"][index - 1],
            features["close"][index],
            features["bollinger_lower"][index - 1],
            features["bollinger_lower"][index],
            features["bollinger_mid"][index - 1],
            features["bollinger_mid"][index],
        )
        if any(value is None for value in values):
            return None

        previous_close, current_close, previous_lower, current_lower, previous_mid, current_mid = values
        if previous_close >= previous_lower and current_close < current_lower:
            return self._build_signal(
                TradeSide.BUY,
                index,
                ["bollinger_lower_reversion_entry"],
            )
        if (
            self.parameters["exit_midline"]
            and previous_close <= previous_mid
            and current_close > current_mid
        ):
            return self._build_signal(
                TradeSide.SELL,
                index,
                ["bollinger_midline_exit"],
            )
        return None


def create_candidate_strategy(
    strategy_id: str,
    parameters: dict[str, Any] | None = None,
) -> CandidateStrategySpec:
    normalized_id = _normalize_strategy_id(strategy_id)
    normalized_parameters = normalize_candidate_strategy_parameters(
        normalized_id,
        parameters or {},
    )

    if normalized_id == "ma_crossover":
        return CandidateStrategySpec(
            strategy_id=normalized_id,
            strategy_version="1.0",
            parameters=normalized_parameters,
            strategy=MACrossoverStrategy(strategy_id=normalized_id),
            engine_fast_window=normalized_parameters["fast_window"],
            engine_slow_window=normalized_parameters["slow_window"],
        )

    if normalized_id == "ema_trend_filter":
        return CandidateStrategySpec(
            strategy_id=normalized_id,
            strategy_version="1.0",
            parameters=normalized_parameters,
            strategy=EMATrendFilterStrategy(normalized_id, "1.0", normalized_parameters),
            feature_pipeline=_ema_trend_feature_pipeline(normalized_parameters),
            regime_context_contract=candidate_regime_context_contract(),
        )

    if normalized_id == "donchian_breakout":
        return CandidateStrategySpec(
            strategy_id=normalized_id,
            strategy_version="1.0",
            parameters=normalized_parameters,
            strategy=DonchianBreakoutStrategy(normalized_id, "1.0", normalized_parameters),
            feature_pipeline=_donchian_feature_pipeline(normalized_parameters),
        )

    if normalized_id == "keltner_breakout":
        return CandidateStrategySpec(
            strategy_id=normalized_id,
            strategy_version="1.0",
            parameters=normalized_parameters,
            strategy=KeltnerBreakoutStrategy(normalized_id, "1.0", normalized_parameters),
            feature_pipeline=_keltner_feature_pipeline(normalized_parameters),
        )

    if normalized_id == "volume_breakout":
        return CandidateStrategySpec(
            strategy_id=normalized_id,
            strategy_version="1.0",
            parameters=normalized_parameters,
            strategy=VolumeBreakoutStrategy(normalized_id, "1.0", normalized_parameters),
            feature_pipeline=_volume_breakout_feature_pipeline(normalized_parameters),
        )

    if normalized_id == "macd_momentum":
        return CandidateStrategySpec(
            strategy_id=normalized_id,
            strategy_version="1.0",
            parameters=normalized_parameters,
            strategy=MACDMomentumStrategy(normalized_id, "1.0", normalized_parameters),
            feature_pipeline=_macd_momentum_feature_pipeline(normalized_parameters),
        )

    if normalized_id == "roc_momentum":
        return CandidateStrategySpec(
            strategy_id=normalized_id,
            strategy_version="1.0",
            parameters=normalized_parameters,
            strategy=ROCMomentumStrategy(normalized_id, "1.0", normalized_parameters),
            feature_pipeline=_roc_momentum_feature_pipeline(normalized_parameters),
        )

    if normalized_id == "stochastic_reversion":
        return CandidateStrategySpec(
            strategy_id=normalized_id,
            strategy_version="1.0",
            parameters=normalized_parameters,
            strategy=StochasticReversionStrategy(normalized_id, "1.0", normalized_parameters),
            feature_pipeline=_stochastic_feature_pipeline(normalized_parameters),
        )

    if normalized_id == "ema_pullback_reentry":
        return CandidateStrategySpec(
            strategy_id=normalized_id,
            strategy_version="1.0",
            parameters=normalized_parameters,
            strategy=EMAPullbackReentryStrategy(normalized_id, "1.0", normalized_parameters),
            feature_pipeline=_ema_pullback_reentry_feature_pipeline(normalized_parameters),
        )

    if normalized_id == "atr_channel_reversion":
        return CandidateStrategySpec(
            strategy_id=normalized_id,
            strategy_version="1.0",
            parameters=normalized_parameters,
            strategy=ATRChannelReversionStrategy(normalized_id, "1.0", normalized_parameters),
            feature_pipeline=_atr_channel_reversion_feature_pipeline(normalized_parameters),
        )

    if normalized_id == "gap_reversal":
        return CandidateStrategySpec(
            strategy_id=normalized_id,
            strategy_version="1.0",
            parameters=normalized_parameters,
            strategy=GapReversalStrategy(normalized_id, "1.0", normalized_parameters),
            feature_pipeline=_gap_reversal_feature_pipeline(normalized_parameters),
        )

    if normalized_id == "gap_continuation_breakout":
        return CandidateStrategySpec(
            strategy_id=normalized_id,
            strategy_version="1.0",
            parameters=normalized_parameters,
            strategy=GapContinuationBreakoutStrategy(
                normalized_id,
                "1.0",
                normalized_parameters,
            ),
            feature_pipeline=_gap_continuation_breakout_feature_pipeline(
                normalized_parameters
            ),
        )

    if normalized_id == "liquidity_sweep_reversal":
        return CandidateStrategySpec(
            strategy_id=normalized_id,
            strategy_version="1.0",
            parameters=normalized_parameters,
            strategy=LiquiditySweepReversalStrategy(
                normalized_id,
                "1.0",
                normalized_parameters,
            ),
            feature_pipeline=_liquidity_sweep_reversal_feature_pipeline(
                normalized_parameters
            ),
        )

    if normalized_id == "volatility_squeeze_breakout":
        return CandidateStrategySpec(
            strategy_id=normalized_id,
            strategy_version="1.0",
            parameters=normalized_parameters,
            strategy=VolatilitySqueezeBreakoutStrategy(
                normalized_id,
                "1.0",
                normalized_parameters,
            ),
            feature_pipeline=_volatility_squeeze_breakout_feature_pipeline(
                normalized_parameters
            ),
        )

    if normalized_id == "range_compression_breakout":
        return CandidateStrategySpec(
            strategy_id=normalized_id,
            strategy_version="1.0",
            parameters=normalized_parameters,
            strategy=RangeCompressionBreakoutStrategy(
                normalized_id,
                "1.0",
                normalized_parameters,
            ),
            feature_pipeline=_range_compression_breakout_feature_pipeline(
                normalized_parameters
            ),
        )

    if normalized_id == "trend_pullback_breakout":
        return CandidateStrategySpec(
            strategy_id=normalized_id,
            strategy_version="1.0",
            parameters=normalized_parameters,
            strategy=TrendPullbackBreakoutStrategy(
                normalized_id,
                "1.0",
                normalized_parameters,
            ),
            feature_pipeline=_trend_pullback_breakout_feature_pipeline(
                normalized_parameters
            ),
            regime_context_contract=candidate_regime_context_contract(),
        )

    if normalized_id == "chandelier_breakout":
        return CandidateStrategySpec(
            strategy_id=normalized_id,
            strategy_version="1.0",
            parameters=normalized_parameters,
            strategy=ChandelierBreakoutStrategy(
                normalized_id,
                "1.0",
                normalized_parameters,
            ),
            feature_pipeline=_chandelier_breakout_feature_pipeline(
                normalized_parameters
            ),
        )

    if normalized_id == "rolling_vwap_reversion":
        return CandidateStrategySpec(
            strategy_id=normalized_id,
            strategy_version="1.0",
            parameters=normalized_parameters,
            strategy=RollingVWAPReversionStrategy(
                normalized_id,
                "1.0",
                normalized_parameters,
            ),
            feature_pipeline=_rolling_vwap_reversion_feature_pipeline(
                normalized_parameters
            ),
            regime_context_contract=candidate_regime_context_contract(),
        )

    if normalized_id == "rsi_mean_reversion":
        return CandidateStrategySpec(
            strategy_id=normalized_id,
            strategy_version="1.0",
            parameters=normalized_parameters,
            strategy=RSIMeanReversionStrategy(normalized_id, "1.0", normalized_parameters),
            feature_pipeline=_rsi_feature_pipeline(normalized_parameters),
        )

    if normalized_id == "bollinger_reversion":
        return CandidateStrategySpec(
            strategy_id=normalized_id,
            strategy_version="1.0",
            parameters=normalized_parameters,
            strategy=BollingerReversionStrategy(normalized_id, "1.0", normalized_parameters),
            feature_pipeline=_bollinger_feature_pipeline(normalized_parameters),
        )

    raise ValueError(f"unsupported_candidate_strategy: {strategy_id}")


def normalize_candidate_strategy_parameters(
    strategy_id: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_id = _normalize_strategy_id(strategy_id)
    raw = dict(parameters or {})

    if normalized_id == "ma_crossover":
        _reject_unknown_parameters(raw, {"fast_window", "slow_window"})
        fast_window = _int_param(raw, "fast_window", 1, minimum=1)
        slow_window = _int_param(raw, "slow_window", 2, minimum=1)
        if slow_window <= fast_window:
            raise ValueError(
                "invalid_candidate_strategy_parameters: slow_window must be greater than fast_window"
            )
        return {
            "fast_window": fast_window,
            "slow_window": slow_window,
        }

    if normalized_id == "ema_trend_filter":
        _reject_unknown_parameters(
            raw,
            {
                "fast_window",
                "slow_window",
                "atr_window",
                "volatility_window",
                "max_atr_pct",
                "max_volatility_pct",
                "min_trend_strength",
            }
            | _REGIME_FILTER_PARAMETER_NAMES,
        )
        fast_window = _int_param(raw, "fast_window", 5, minimum=1)
        slow_window = _int_param(raw, "slow_window", 21, minimum=1)
        if slow_window <= fast_window:
            raise ValueError(
                "invalid_candidate_strategy_parameters: slow_window must be greater than fast_window"
            )
        values = {
            "fast_window": fast_window,
            "slow_window": slow_window,
            "atr_window": _int_param(raw, "atr_window", 14, minimum=1),
            "volatility_window": _int_param(raw, "volatility_window", 20, minimum=1),
            "max_atr_pct": _float_param(raw, "max_atr_pct", 0.08, minimum=0.0),
            "max_volatility_pct": _float_param(raw, "max_volatility_pct", 0.08, minimum=0.0),
            "min_trend_strength": _float_param(raw, "min_trend_strength", 0.0, minimum=0.0),
        }
        values.update(_regime_filter_parameters(raw))
        return values

    if normalized_id == "donchian_breakout":
        _reject_unknown_parameters(raw, {"channel_window", "exit_window"})
        return {
            "channel_window": _int_param(raw, "channel_window", 20, minimum=1),
            "exit_window": _int_param(raw, "exit_window", 10, minimum=1),
        }

    if normalized_id == "keltner_breakout":
        _reject_unknown_parameters(
            raw,
            {"ema_window", "atr_window", "atr_multiplier", "exit_midline"},
        )
        return {
            "ema_window": _int_param(raw, "ema_window", 20, minimum=1),
            "atr_window": _int_param(raw, "atr_window", 14, minimum=1),
            "atr_multiplier": _float_param(raw, "atr_multiplier", 1.5, minimum=0.01),
            "exit_midline": _bool_param(raw, "exit_midline", True),
        }

    if normalized_id == "volume_breakout":
        _reject_unknown_parameters(
            raw,
            {
                "price_window",
                "volume_window",
                "min_volume_ratio",
                "exit_window",
                "exit_on_breakdown",
            },
        )
        return {
            "price_window": _int_param(raw, "price_window", 20, minimum=2),
            "volume_window": _int_param(raw, "volume_window", 20, minimum=2),
            "min_volume_ratio": _float_param(
                raw,
                "min_volume_ratio",
                1.2,
                minimum=0.01,
            ),
            "exit_window": _int_param(raw, "exit_window", 10, minimum=1),
            "exit_on_breakdown": _bool_param(raw, "exit_on_breakdown", True),
        }

    if normalized_id == "macd_momentum":
        _reject_unknown_parameters(
            raw,
            {
                "fast_window",
                "slow_window",
                "signal_window",
                "atr_window",
                "min_histogram_pct",
                "max_atr_pct",
                "exit_on_signal_cross",
            },
        )
        fast_window = _int_param(raw, "fast_window", 12, minimum=1)
        slow_window = _int_param(raw, "slow_window", 26, minimum=1)
        if slow_window <= fast_window:
            raise ValueError(
                "invalid_candidate_strategy_parameters: slow_window must be greater than fast_window"
            )
        return {
            "fast_window": fast_window,
            "slow_window": slow_window,
            "signal_window": _int_param(raw, "signal_window", 9, minimum=1),
            "atr_window": _int_param(raw, "atr_window", 14, minimum=1),
            "min_histogram_pct": _float_param(
                raw,
                "min_histogram_pct",
                0.0,
                minimum=0.0,
            ),
            "max_atr_pct": _float_param(raw, "max_atr_pct", 0.08, minimum=0.0),
            "exit_on_signal_cross": _bool_param(raw, "exit_on_signal_cross", True),
        }

    if normalized_id == "roc_momentum":
        _reject_unknown_parameters(
            raw,
            {
                "roc_window",
                "trend_window",
                "atr_window",
                "min_roc_pct",
                "max_atr_pct",
                "exit_roc_pct",
                "exit_on_trend_loss",
            },
        )
        values = {
            "roc_window": _int_param(raw, "roc_window", 12, minimum=1),
            "trend_window": _int_param(raw, "trend_window", 34, minimum=1),
            "atr_window": _int_param(raw, "atr_window", 14, minimum=1),
            "min_roc_pct": _float_param(raw, "min_roc_pct", 0.01, minimum=0.0),
            "max_atr_pct": _float_param(raw, "max_atr_pct", 0.08, minimum=0.0),
            "exit_roc_pct": _float_param(raw, "exit_roc_pct", 0.0, minimum=-1.0),
            "exit_on_trend_loss": _bool_param(raw, "exit_on_trend_loss", True),
        }
        if values["exit_roc_pct"] >= values["min_roc_pct"]:
            raise ValueError(
                "invalid_candidate_strategy_parameters: exit_roc_pct must be below min_roc_pct"
            )
        return values

    if normalized_id == "stochastic_reversion":
        _reject_unknown_parameters(
            raw,
            {
                "k_window",
                "d_window",
                "oversold",
                "overbought",
                "exit_k",
                "exit_on_midline",
            },
        )
        values = {
            "k_window": _int_param(raw, "k_window", 14, minimum=2),
            "d_window": _int_param(raw, "d_window", 3, minimum=1),
            "oversold": _float_param(raw, "oversold", 20.0, minimum=0.0, maximum=100.0),
            "overbought": _float_param(raw, "overbought", 80.0, minimum=0.0, maximum=100.0),
            "exit_k": _float_param(raw, "exit_k", 50.0, minimum=0.0, maximum=100.0),
            "exit_on_midline": _bool_param(raw, "exit_on_midline", True),
        }
        if values["oversold"] >= values["overbought"]:
            raise ValueError(
                "invalid_candidate_strategy_parameters: oversold must be below overbought"
            )
        if not values["oversold"] < values["exit_k"] < values["overbought"]:
            raise ValueError(
                "invalid_candidate_strategy_parameters: exit_k must be between oversold and overbought"
            )
        return values

    if normalized_id == "ema_pullback_reentry":
        _reject_unknown_parameters(
            raw,
            {
                "fast_window",
                "slow_window",
                "rsi_window",
                "pullback_rsi",
                "reentry_rsi",
                "exit_rsi",
                "atr_window",
                "max_atr_pct",
                "exit_on_trend_loss",
            },
        )
        values = {
            "fast_window": _int_param(raw, "fast_window", 13, minimum=1),
            "slow_window": _int_param(raw, "slow_window", 55, minimum=1),
            "rsi_window": _int_param(raw, "rsi_window", 14, minimum=1),
            "pullback_rsi": _float_param(
                raw,
                "pullback_rsi",
                40.0,
                minimum=0.0,
                maximum=100.0,
            ),
            "reentry_rsi": _float_param(
                raw,
                "reentry_rsi",
                52.0,
                minimum=0.0,
                maximum=100.0,
            ),
            "exit_rsi": _float_param(
                raw,
                "exit_rsi",
                70.0,
                minimum=0.0,
                maximum=100.0,
            ),
            "atr_window": _int_param(raw, "atr_window", 14, minimum=1),
            "max_atr_pct": _float_param(raw, "max_atr_pct", 0.08, minimum=0.0),
            "exit_on_trend_loss": _bool_param(raw, "exit_on_trend_loss", True),
        }
        if values["slow_window"] <= values["fast_window"]:
            raise ValueError(
                "invalid_candidate_strategy_parameters: slow_window must be greater than fast_window"
            )
        if not values["pullback_rsi"] < values["reentry_rsi"] < values["exit_rsi"]:
            raise ValueError(
                "invalid_candidate_strategy_parameters: pullback_rsi must be below reentry_rsi below exit_rsi"
            )
        return values

    if normalized_id == "atr_channel_reversion":
        _reject_unknown_parameters(
            raw,
            {
                "ema_window",
                "atr_window",
                "atr_multiplier",
                "min_atr_pct",
                "max_atr_pct",
                "exit_midline",
            },
        )
        values = {
            "ema_window": _int_param(raw, "ema_window", 20, minimum=1),
            "atr_window": _int_param(raw, "atr_window", 14, minimum=1),
            "atr_multiplier": _float_param(raw, "atr_multiplier", 1.5, minimum=0.01),
            "min_atr_pct": _float_param(raw, "min_atr_pct", 0.0, minimum=0.0),
            "max_atr_pct": _float_param(raw, "max_atr_pct", 0.08, minimum=0.0),
            "exit_midline": _bool_param(raw, "exit_midline", True),
        }
        if values["min_atr_pct"] > values["max_atr_pct"]:
            raise ValueError(
                "invalid_candidate_strategy_parameters: min_atr_pct must not exceed max_atr_pct"
            )
        return values

    if normalized_id == "gap_reversal":
        _reject_unknown_parameters(
            raw,
            {
                "atr_window",
                "volume_window",
                "min_gap_pct",
                "min_reclaim_ratio",
                "min_volume_ratio",
                "max_atr_pct",
                "exit_on_up_gap",
            },
        )
        return {
            "atr_window": _int_param(raw, "atr_window", 14, minimum=1),
            "volume_window": _int_param(raw, "volume_window", 20, minimum=2),
            "min_gap_pct": _float_param(raw, "min_gap_pct", 0.002, minimum=0.0),
            "min_reclaim_ratio": _float_param(
                raw,
                "min_reclaim_ratio",
                0.35,
                minimum=0.0,
            ),
            "min_volume_ratio": _float_param(raw, "min_volume_ratio", 1.0, minimum=0.0),
            "max_atr_pct": _float_param(raw, "max_atr_pct", 0.08, minimum=0.0),
            "exit_on_up_gap": _bool_param(raw, "exit_on_up_gap", True),
        }

    if normalized_id == "gap_continuation_breakout":
        _reject_unknown_parameters(
            raw,
            {
                "atr_window",
                "volume_window",
                "min_gap_pct",
                "min_follow_through_ratio",
                "min_volume_ratio",
                "min_atr_pct",
                "max_atr_pct",
                "exit_on_down_gap",
            },
        )
        values = {
            "atr_window": _int_param(raw, "atr_window", 14, minimum=1),
            "volume_window": _int_param(raw, "volume_window", 20, minimum=2),
            "min_gap_pct": _float_param(raw, "min_gap_pct", 0.002, minimum=0.0),
            "min_follow_through_ratio": _float_param(
                raw,
                "min_follow_through_ratio",
                0.25,
                minimum=0.0,
            ),
            "min_volume_ratio": _float_param(raw, "min_volume_ratio", 1.0, minimum=0.0),
            "min_atr_pct": _float_param(raw, "min_atr_pct", 0.0, minimum=0.0),
            "max_atr_pct": _float_param(raw, "max_atr_pct", 0.08, minimum=0.0),
            "exit_on_down_gap": _bool_param(raw, "exit_on_down_gap", True),
        }
        if values["min_atr_pct"] > values["max_atr_pct"]:
            raise ValueError(
                "invalid_candidate_strategy_parameters: min_atr_pct must not exceed max_atr_pct"
            )
        return values

    if normalized_id == "liquidity_sweep_reversal":
        _reject_unknown_parameters(
            raw,
            {
                "range_window",
                "atr_window",
                "volume_window",
                "min_sweep_pct",
                "min_close_position",
                "min_volume_ratio",
                "min_atr_pct",
                "max_atr_pct",
                "exit_on_bearish_sweep",
            },
        )
        values = {
            "range_window": _int_param(raw, "range_window", 20, minimum=2),
            "atr_window": _int_param(raw, "atr_window", 14, minimum=1),
            "volume_window": _int_param(raw, "volume_window", 20, minimum=2),
            "min_sweep_pct": _float_param(raw, "min_sweep_pct", 0.002, minimum=0.0),
            "min_close_position": _float_param(
                raw,
                "min_close_position",
                0.6,
                minimum=0.0,
                maximum=1.0,
            ),
            "min_volume_ratio": _float_param(raw, "min_volume_ratio", 1.0, minimum=0.0),
            "min_atr_pct": _float_param(raw, "min_atr_pct", 0.0, minimum=0.0),
            "max_atr_pct": _float_param(raw, "max_atr_pct", 0.08, minimum=0.0),
            "exit_on_bearish_sweep": _bool_param(raw, "exit_on_bearish_sweep", True),
        }
        if values["min_atr_pct"] > values["max_atr_pct"]:
            raise ValueError(
                "invalid_candidate_strategy_parameters: min_atr_pct must not exceed max_atr_pct"
            )
        return values

    if normalized_id == "volatility_squeeze_breakout":
        _reject_unknown_parameters(
            raw,
            {
                "breakout_window",
                "bb_window",
                "squeeze_window",
                "bandwidth_stddev",
                "max_squeeze_ratio",
                "min_volume_ratio",
                "atr_window",
                "min_atr_pct",
                "max_atr_pct",
                "exit_on_midline_loss",
            },
        )
        values = {
            "breakout_window": _int_param(raw, "breakout_window", 20, minimum=2),
            "bb_window": _int_param(raw, "bb_window", 20, minimum=2),
            "squeeze_window": _int_param(raw, "squeeze_window", 20, minimum=2),
            "bandwidth_stddev": _float_param(raw, "bandwidth_stddev", 2.0, minimum=0.01),
            "max_squeeze_ratio": _float_param(raw, "max_squeeze_ratio", 0.85, minimum=0.0),
            "min_volume_ratio": _float_param(raw, "min_volume_ratio", 1.0, minimum=0.0),
            "atr_window": _int_param(raw, "atr_window", 14, minimum=1),
            "min_atr_pct": _float_param(raw, "min_atr_pct", 0.0, minimum=0.0),
            "max_atr_pct": _float_param(raw, "max_atr_pct", 0.08, minimum=0.0),
            "exit_on_midline_loss": _bool_param(raw, "exit_on_midline_loss", True),
        }
        if values["min_atr_pct"] > values["max_atr_pct"]:
            raise ValueError(
                "invalid_candidate_strategy_parameters: min_atr_pct must not exceed max_atr_pct"
            )
        return values

    if normalized_id == "range_compression_breakout":
        _reject_unknown_parameters(
            raw,
            {
                "breakout_window",
                "compression_window",
                "volume_window",
                "atr_window",
                "max_range_width_pct",
                "max_compression_ratio",
                "min_volume_ratio",
                "min_atr_pct",
                "max_atr_pct",
                "exit_on_midline_loss",
            },
        )
        values = {
            "breakout_window": _int_param(raw, "breakout_window", 20, minimum=2),
            "compression_window": _int_param(raw, "compression_window", 20, minimum=2),
            "volume_window": _int_param(raw, "volume_window", 20, minimum=2),
            "atr_window": _int_param(raw, "atr_window", 14, minimum=1),
            "max_range_width_pct": _float_param(
                raw,
                "max_range_width_pct",
                0.02,
                minimum=0.0,
            ),
            "max_compression_ratio": _float_param(
                raw,
                "max_compression_ratio",
                0.8,
                minimum=0.0,
            ),
            "min_volume_ratio": _float_param(raw, "min_volume_ratio", 1.0, minimum=0.0),
            "min_atr_pct": _float_param(raw, "min_atr_pct", 0.0, minimum=0.0),
            "max_atr_pct": _float_param(raw, "max_atr_pct", 0.08, minimum=0.0),
            "exit_on_midline_loss": _bool_param(raw, "exit_on_midline_loss", True),
        }
        if values["min_atr_pct"] > values["max_atr_pct"]:
            raise ValueError(
                "invalid_candidate_strategy_parameters: min_atr_pct must not exceed max_atr_pct"
            )
        return values

    if normalized_id == "trend_pullback_breakout":
        _reject_unknown_parameters(
            raw,
            {
                "fast_window",
                "slow_window",
                "breakout_window",
                "pullback_window",
                "volume_window",
                "atr_window",
                "min_pullback_depth_pct",
                "max_pullback_depth_pct",
                "min_trend_spread_pct",
                "min_volume_ratio",
                "min_atr_pct",
                "max_atr_pct",
                "exit_on_fast_ema_loss",
            }
            | _REGIME_FILTER_PARAMETER_NAMES,
        )
        fast_window = _int_param(raw, "fast_window", 13, minimum=1)
        slow_window = _int_param(raw, "slow_window", 55, minimum=1)
        values = {
            "fast_window": fast_window,
            "slow_window": slow_window,
            "breakout_window": _int_param(raw, "breakout_window", 20, minimum=2),
            "pullback_window": _int_param(raw, "pullback_window", 10, minimum=2),
            "volume_window": _int_param(raw, "volume_window", 20, minimum=2),
            "atr_window": _int_param(raw, "atr_window", 14, minimum=1),
            "min_pullback_depth_pct": _float_param(
                raw,
                "min_pullback_depth_pct",
                0.002,
                minimum=0.0,
            ),
            "max_pullback_depth_pct": _float_param(
                raw,
                "max_pullback_depth_pct",
                0.04,
                minimum=0.0,
            ),
            "min_trend_spread_pct": _float_param(
                raw,
                "min_trend_spread_pct",
                0.0,
                minimum=0.0,
            ),
            "min_volume_ratio": _float_param(raw, "min_volume_ratio", 1.0, minimum=0.0),
            "min_atr_pct": _float_param(raw, "min_atr_pct", 0.0, minimum=0.0),
            "max_atr_pct": _float_param(raw, "max_atr_pct", 0.08, minimum=0.0),
            "exit_on_fast_ema_loss": _bool_param(raw, "exit_on_fast_ema_loss", True),
        }
        values.update(_regime_filter_parameters(raw))
        if values["slow_window"] <= values["fast_window"]:
            raise ValueError(
                "invalid_candidate_strategy_parameters: slow_window must be greater than fast_window"
            )
        if values["min_pullback_depth_pct"] > values["max_pullback_depth_pct"]:
            raise ValueError(
                "invalid_candidate_strategy_parameters: min_pullback_depth_pct must not exceed max_pullback_depth_pct"
            )
        if values["min_atr_pct"] > values["max_atr_pct"]:
            raise ValueError(
                "invalid_candidate_strategy_parameters: min_atr_pct must not exceed max_atr_pct"
            )
        return values

    if normalized_id == "chandelier_breakout":
        _reject_unknown_parameters(
            raw,
            {
                "entry_window",
                "exit_window",
                "atr_window",
                "atr_multiplier",
                "volume_window",
                "min_volume_ratio",
                "min_atr_pct",
                "max_atr_pct",
                "exit_on_chandelier_loss",
            },
        )
        values = {
            "entry_window": _int_param(raw, "entry_window", 20, minimum=2),
            "exit_window": _int_param(raw, "exit_window", 20, minimum=2),
            "atr_window": _int_param(raw, "atr_window", 14, minimum=1),
            "atr_multiplier": _float_param(raw, "atr_multiplier", 2.5, minimum=0.01),
            "volume_window": _int_param(raw, "volume_window", 20, minimum=2),
            "min_volume_ratio": _float_param(raw, "min_volume_ratio", 1.0, minimum=0.0),
            "min_atr_pct": _float_param(raw, "min_atr_pct", 0.0, minimum=0.0),
            "max_atr_pct": _float_param(raw, "max_atr_pct", 0.08, minimum=0.0),
            "exit_on_chandelier_loss": _bool_param(
                raw,
                "exit_on_chandelier_loss",
                True,
            ),
        }
        if values["min_atr_pct"] > values["max_atr_pct"]:
            raise ValueError(
                "invalid_candidate_strategy_parameters: min_atr_pct must not exceed max_atr_pct"
            )
        return values

    if normalized_id == "rolling_vwap_reversion":
        _reject_unknown_parameters(
            raw,
            {
                "vwap_window",
                "volume_window",
                "atr_window",
                "entry_band_pct",
                "min_volume_ratio",
                "min_atr_pct",
                "max_atr_pct",
                "exit_on_vwap_reclaim",
            }
            | _REGIME_FILTER_PARAMETER_NAMES,
        )
        values = {
            "vwap_window": _int_param(raw, "vwap_window", 20, minimum=2),
            "volume_window": _int_param(raw, "volume_window", 20, minimum=2),
            "atr_window": _int_param(raw, "atr_window", 14, minimum=1),
            "entry_band_pct": _float_param(
                raw,
                "entry_band_pct",
                0.01,
                minimum=0.0,
            ),
            "min_volume_ratio": _float_param(raw, "min_volume_ratio", 0.8, minimum=0.0),
            "min_atr_pct": _float_param(raw, "min_atr_pct", 0.0, minimum=0.0),
            "max_atr_pct": _float_param(raw, "max_atr_pct", 0.08, minimum=0.0),
            "exit_on_vwap_reclaim": _bool_param(
                raw,
                "exit_on_vwap_reclaim",
                True,
            ),
        }
        values.update(_regime_filter_parameters(raw))
        if values["min_atr_pct"] > values["max_atr_pct"]:
            raise ValueError(
                "invalid_candidate_strategy_parameters: min_atr_pct must not exceed max_atr_pct"
            )
        return values

    if normalized_id == "rsi_mean_reversion":
        _reject_unknown_parameters(raw, {"rsi_window", "oversold", "overbought", "exit_rsi"})
        values = {
            "rsi_window": _int_param(raw, "rsi_window", 14, minimum=1),
            "oversold": _float_param(raw, "oversold", 30.0, minimum=0.0, maximum=100.0),
            "overbought": _float_param(raw, "overbought", 70.0, minimum=0.0, maximum=100.0),
            "exit_rsi": _float_param(raw, "exit_rsi", 50.0, minimum=0.0, maximum=100.0),
        }
        if values["oversold"] >= values["overbought"]:
            raise ValueError(
                "invalid_candidate_strategy_parameters: oversold must be below overbought"
            )
        return values

    if normalized_id == "bollinger_reversion":
        _reject_unknown_parameters(raw, {"window", "stddev", "exit_midline"})
        return {
            "window": _int_param(raw, "window", 20, minimum=2),
            "stddev": _float_param(raw, "stddev", 2.0, minimum=0.01),
            "exit_midline": _bool_param(raw, "exit_midline", True),
        }

    raise ValueError(f"unsupported_candidate_strategy: {strategy_id}")


def _ema_trend_feature_pipeline(parameters: dict[str, Any]) -> FeaturePipeline:
    def pipeline(data):
        closes = [float(item.close) for item in data]
        fast = _ema_series(closes, parameters["fast_window"])
        slow = _ema_series(closes, parameters["slow_window"])
        atr_pct = _atr_pct_series(data, parameters["atr_window"])
        volatility = _return_volatility_pct_series(closes, parameters["volatility_window"])
        trend_strength = []
        for fast_value, slow_value, atr_value, close in zip(fast, slow, atr_pct, closes):
            if fast_value is None or slow_value is None or atr_value is None or close == 0.0:
                trend_strength.append(None)
                continue
            denominator = max(abs(close) * max(atr_value, 0.000001), 0.000001)
            trend_strength.append(abs(float(fast_value) - float(slow_value)) / denominator)
        features = {
            "fast_ema": fast,
            "slow_ema": slow,
            "atr_pct": atr_pct,
            "return_volatility_pct": volatility,
            "trend_strength": trend_strength,
        }
        features.update(_candidate_regime_context_features(data, parameters))
        return features

    return pipeline


def _donchian_feature_pipeline(parameters: dict[str, Any]) -> FeaturePipeline:
    def pipeline(data):
        highs = [float(item.high) for item in data]
        lows = [float(item.low) for item in data]
        return {
            "close": [float(item.close) for item in data],
            "donchian_high": _rolling_extreme_excluding_current(
                highs,
                parameters["channel_window"],
                max,
            ),
            "donchian_low": _rolling_extreme_excluding_current(
                lows,
                parameters["channel_window"],
                min,
            ),
            "exit_low": _rolling_extreme_excluding_current(
                lows,
                parameters["exit_window"],
                min,
            ),
        }

    return pipeline


def _keltner_feature_pipeline(parameters: dict[str, Any]) -> FeaturePipeline:
    def pipeline(data):
        closes = [float(item.close) for item in data]
        mid = _ema_series(closes, parameters["ema_window"])
        atr = _atr_series(data, parameters["atr_window"])
        lower = []
        upper = []
        for mid_value, atr_value in zip(mid, atr):
            if mid_value is None or atr_value is None:
                lower.append(None)
                upper.append(None)
                continue
            lower.append(mid_value - parameters["atr_multiplier"] * atr_value)
            upper.append(mid_value + parameters["atr_multiplier"] * atr_value)
        return {
            "close": closes,
            "keltner_mid": mid,
            "keltner_lower": lower,
            "keltner_upper": upper,
            "atr": atr,
        }

    return pipeline


def _rsi_feature_pipeline(parameters: dict[str, Any]) -> FeaturePipeline:
    def pipeline(data):
        closes = [float(item.close) for item in data]
        return {
            "close": closes,
            "rsi": _rsi_series(closes, parameters["rsi_window"]),
        }

    return pipeline


def _volume_breakout_feature_pipeline(parameters: dict[str, Any]) -> FeaturePipeline:
    def pipeline(data):
        closes = [float(item.close) for item in data]
        highs = [float(item.high) for item in data]
        lows = [float(item.low) for item in data]
        volumes = [float(item.volume) for item in data]
        volume_ma = _rolling_mean_excluding_current(volumes, parameters["volume_window"])
        volume_ratio = []
        for volume, average_volume in zip(volumes, volume_ma):
            if average_volume is None or average_volume <= 0.0:
                volume_ratio.append(None)
                continue
            volume_ratio.append(volume / average_volume)
        return {
            "close": closes,
            "volume": volumes,
            "breakout_high": _rolling_extreme_excluding_current(
                highs,
                parameters["price_window"],
                max,
            ),
            "exit_low": _rolling_extreme_excluding_current(
                lows,
                parameters["exit_window"],
                min,
            ),
            "volume_ma": volume_ma,
            "volume_ratio": volume_ratio,
        }

    return pipeline


def _macd_momentum_feature_pipeline(parameters: dict[str, Any]) -> FeaturePipeline:
    def pipeline(data):
        closes = [float(item.close) for item in data]
        fast = _ema_series(closes, parameters["fast_window"])
        slow = _ema_series(closes, parameters["slow_window"])
        macd_line: list[float | None] = []
        for fast_value, slow_value in zip(fast, slow):
            if fast_value is None or slow_value is None:
                macd_line.append(None)
                continue
            macd_line.append(float(fast_value) - float(slow_value))
        macd_signal = _ema_optional_series(macd_line, parameters["signal_window"])
        macd_histogram: list[float | None] = []
        macd_histogram_pct: list[float | None] = []
        for close, line_value, signal_value in zip(closes, macd_line, macd_signal):
            if line_value is None or signal_value is None:
                macd_histogram.append(None)
                macd_histogram_pct.append(None)
                continue
            histogram = line_value - signal_value
            macd_histogram.append(histogram)
            if close == 0.0:
                macd_histogram_pct.append(None)
            else:
                macd_histogram_pct.append(histogram / abs(close))
        return {
            "close": closes,
            "macd_line": macd_line,
            "macd_signal": macd_signal,
            "macd_histogram": macd_histogram,
            "macd_histogram_pct": macd_histogram_pct,
            "atr_pct": _atr_pct_series(data, parameters["atr_window"]),
        }

    return pipeline


def _roc_momentum_feature_pipeline(parameters: dict[str, Any]) -> FeaturePipeline:
    def pipeline(data):
        closes = [float(item.close) for item in data]
        roc_pct: list[float | None] = []
        for index, close in enumerate(closes):
            if index < parameters["roc_window"]:
                roc_pct.append(None)
                continue
            previous_close = closes[index - parameters["roc_window"]]
            if previous_close == 0.0:
                roc_pct.append(None)
                continue
            roc_pct.append((close - previous_close) / abs(previous_close))
        return {
            "close": closes,
            "roc_pct": roc_pct,
            "trend_ema": _ema_series(closes, parameters["trend_window"]),
            "atr_pct": _atr_pct_series(data, parameters["atr_window"]),
        }

    return pipeline


def _stochastic_feature_pipeline(parameters: dict[str, Any]) -> FeaturePipeline:
    def pipeline(data):
        highs = [float(item.high) for item in data]
        lows = [float(item.low) for item in data]
        closes = [float(item.close) for item in data]
        stochastic_k: list[float | None] = []
        for index, close in enumerate(closes):
            if index + 1 < parameters["k_window"]:
                stochastic_k.append(None)
                continue
            high_window = highs[index + 1 - parameters["k_window"] : index + 1]
            low_window = lows[index + 1 - parameters["k_window"] : index + 1]
            high = max(high_window)
            low = min(low_window)
            if high == low:
                stochastic_k.append(50.0)
            else:
                stochastic_k.append(100.0 * (close - low) / (high - low))
        return {
            "close": closes,
            "stochastic_k": stochastic_k,
            "stochastic_d": _rolling_mean_optional_series(stochastic_k, parameters["d_window"]),
        }

    return pipeline


def _ema_pullback_reentry_feature_pipeline(parameters: dict[str, Any]) -> FeaturePipeline:
    def pipeline(data):
        closes = [float(item.close) for item in data]
        return {
            "close": closes,
            "fast_ema": _ema_series(closes, parameters["fast_window"]),
            "slow_ema": _ema_series(closes, parameters["slow_window"]),
            "rsi": _rsi_series(closes, parameters["rsi_window"]),
            "atr_pct": _atr_pct_series(data, parameters["atr_window"]),
        }

    return pipeline


def _atr_channel_reversion_feature_pipeline(parameters: dict[str, Any]) -> FeaturePipeline:
    def pipeline(data):
        closes = [float(item.close) for item in data]
        mid = _ema_series(closes, parameters["ema_window"])
        atr = _atr_series(data, parameters["atr_window"])
        lower = []
        upper = []
        for mid_value, atr_value in zip(mid, atr):
            if mid_value is None or atr_value is None:
                lower.append(None)
                upper.append(None)
                continue
            channel_width = parameters["atr_multiplier"] * atr_value
            lower.append(mid_value - channel_width)
            upper.append(mid_value + channel_width)
        return {
            "close": closes,
            "channel_mid": mid,
            "channel_lower": lower,
            "channel_upper": upper,
            "atr": atr,
            "atr_pct": _atr_pct_series(data, parameters["atr_window"]),
        }

    return pipeline


def _gap_reversal_feature_pipeline(parameters: dict[str, Any]) -> FeaturePipeline:
    def pipeline(data):
        opens = [float(item.open) for item in data]
        closes = [float(item.close) for item in data]
        volumes = [float(item.volume) for item in data]
        volume_ma = _rolling_mean_excluding_current(volumes, parameters["volume_window"])
        previous_closes: list[float | None] = []
        gap_pct: list[float | None] = []
        gap_reclaim_ratio: list[float | None] = []
        volume_ratio: list[float | None] = []
        for index, (open_price, close_price, volume) in enumerate(zip(opens, closes, volumes)):
            previous_close = closes[index - 1] if index > 0 else None
            previous_closes.append(previous_close)
            average_volume = volume_ma[index]
            if previous_close is None or previous_close == 0.0:
                gap_pct.append(None)
                gap_reclaim_ratio.append(None)
            else:
                gap = open_price - previous_close
                gap_pct.append(gap / abs(previous_close))
                gap_size = abs(gap)
                if gap_size == 0.0:
                    gap_reclaim_ratio.append(0.0)
                else:
                    gap_reclaim_ratio.append((close_price - open_price) / gap_size)
            if average_volume is None or average_volume <= 0.0:
                volume_ratio.append(None)
            else:
                volume_ratio.append(volume / average_volume)
        return {
            "open": opens,
            "close": closes,
            "previous_close": previous_closes,
            "gap_pct": gap_pct,
            "gap_reclaim_ratio": gap_reclaim_ratio,
            "volume_ratio": volume_ratio,
            "atr_pct": _atr_pct_series(data, parameters["atr_window"]),
        }

    return pipeline


def _gap_continuation_breakout_feature_pipeline(parameters: dict[str, Any]) -> FeaturePipeline:
    def pipeline(data):
        opens = [float(item.open) for item in data]
        closes = [float(item.close) for item in data]
        volumes = [float(item.volume) for item in data]
        volume_ma = _rolling_mean_excluding_current(volumes, parameters["volume_window"])
        previous_closes: list[float | None] = []
        gap_pct: list[float | None] = []
        gap_follow_through_ratio: list[float | None] = []
        volume_ratio: list[float | None] = []
        for index, (open_price, close_price, volume) in enumerate(zip(opens, closes, volumes)):
            previous_close = closes[index - 1] if index > 0 else None
            previous_closes.append(previous_close)
            average_volume = volume_ma[index]
            if previous_close is None or previous_close == 0.0:
                gap_pct.append(None)
                gap_follow_through_ratio.append(None)
            else:
                gap = open_price - previous_close
                gap_pct.append(gap / abs(previous_close))
                gap_size = abs(gap)
                if gap_size == 0.0:
                    gap_follow_through_ratio.append(0.0)
                else:
                    gap_follow_through_ratio.append((close_price - open_price) / gap_size)
            if average_volume is None or average_volume <= 0.0:
                volume_ratio.append(None)
            else:
                volume_ratio.append(volume / average_volume)
        return {
            "open": opens,
            "close": closes,
            "previous_close": previous_closes,
            "gap_pct": gap_pct,
            "gap_follow_through_ratio": gap_follow_through_ratio,
            "volume_ratio": volume_ratio,
            "atr_pct": _atr_pct_series(data, parameters["atr_window"]),
        }

    return pipeline


def _liquidity_sweep_reversal_feature_pipeline(parameters: dict[str, Any]) -> FeaturePipeline:
    def pipeline(data):
        lows = [float(item.low) for item in data]
        highs = [float(item.high) for item in data]
        closes = [float(item.close) for item in data]
        volumes = [float(item.volume) for item in data]
        volume_ma = _rolling_mean_excluding_current(volumes, parameters["volume_window"])
        volume_ratio: list[float | None] = []
        close_position: list[float | None] = []
        for low, high, close, volume, average_volume in zip(
            lows,
            highs,
            closes,
            volumes,
            volume_ma,
        ):
            candle_range = high - low
            if candle_range <= 0.0:
                close_position.append(None)
            else:
                close_position.append((close - low) / candle_range)
            if average_volume is None or average_volume <= 0.0:
                volume_ratio.append(None)
            else:
                volume_ratio.append(volume / average_volume)
        return {
            "low": lows,
            "high": highs,
            "close": closes,
            "range_low": _rolling_extreme_excluding_current(
                lows,
                parameters["range_window"],
                min,
            ),
            "range_high": _rolling_extreme_excluding_current(
                highs,
                parameters["range_window"],
                max,
            ),
            "close_position": close_position,
            "volume_ratio": volume_ratio,
            "atr_pct": _atr_pct_series(data, parameters["atr_window"]),
        }

    return pipeline


def _volatility_squeeze_breakout_feature_pipeline(parameters: dict[str, Any]) -> FeaturePipeline:
    def pipeline(data):
        closes = [float(item.close) for item in data]
        highs = [float(item.high) for item in data]
        volumes = [float(item.volume) for item in data]
        mid = _rolling_mean_excluding_current(closes, parameters["bb_window"])
        standard_deviation = _rolling_stddev_excluding_current(
            closes,
            parameters["bb_window"],
        )
        bandwidth_pct: list[float | None] = []
        for mid_value, stddev_value in zip(mid, standard_deviation):
            if mid_value is None or stddev_value is None or mid_value == 0.0:
                bandwidth_pct.append(None)
                continue
            bandwidth_pct.append(
                2.0 * parameters["bandwidth_stddev"] * stddev_value / abs(mid_value)
            )

        bandwidth_baseline = _rolling_mean_optional_excluding_current(
            bandwidth_pct,
            parameters["squeeze_window"],
        )
        squeeze_ratio: list[float | None] = []
        for width, baseline in zip(bandwidth_pct, bandwidth_baseline):
            if width is None or baseline is None or baseline <= 0.0:
                squeeze_ratio.append(None)
                continue
            squeeze_ratio.append(width / baseline)

        volume_ma = _rolling_mean_excluding_current(volumes, parameters["bb_window"])
        volume_ratio: list[float | None] = []
        for volume, average_volume in zip(volumes, volume_ma):
            if average_volume is None or average_volume <= 0.0:
                volume_ratio.append(None)
                continue
            volume_ratio.append(volume / average_volume)

        return {
            "close": closes,
            "breakout_high": _rolling_extreme_excluding_current(
                highs,
                parameters["breakout_window"],
                max,
            ),
            "squeeze_mid": mid,
            "bandwidth_pct": bandwidth_pct,
            "squeeze_ratio": squeeze_ratio,
            "volume_ratio": volume_ratio,
            "atr_pct": _atr_pct_series(data, parameters["atr_window"]),
        }

    return pipeline


def _range_compression_breakout_feature_pipeline(parameters: dict[str, Any]) -> FeaturePipeline:
    def pipeline(data):
        closes = [float(item.close) for item in data]
        highs = [float(item.high) for item in data]
        lows = [float(item.low) for item in data]
        volumes = [float(item.volume) for item in data]
        range_high = _rolling_extreme_excluding_current(
            highs,
            parameters["breakout_window"],
            max,
        )
        range_low = _rolling_extreme_excluding_current(
            lows,
            parameters["breakout_window"],
            min,
        )
        range_mid: list[float | None] = []
        range_width_pct: list[float | None] = []
        for close, high, low in zip(closes, range_high, range_low):
            if high is None or low is None or close == 0.0:
                range_mid.append(None)
                range_width_pct.append(None)
                continue
            range_mid.append((high + low) / 2.0)
            range_width_pct.append((high - low) / abs(close))

        range_width_baseline = _rolling_mean_optional_excluding_current(
            range_width_pct,
            parameters["compression_window"],
        )
        range_compression_ratio: list[float | None] = []
        for width, baseline in zip(range_width_pct, range_width_baseline):
            if width is None or baseline is None or baseline <= 0.0:
                range_compression_ratio.append(None)
                continue
            range_compression_ratio.append(width / baseline)

        volume_ma = _rolling_mean_excluding_current(volumes, parameters["volume_window"])
        volume_ratio: list[float | None] = []
        for volume, average_volume in zip(volumes, volume_ma):
            if average_volume is None or average_volume <= 0.0:
                volume_ratio.append(None)
                continue
            volume_ratio.append(volume / average_volume)

        return {
            "close": closes,
            "range_high": range_high,
            "range_low": range_low,
            "range_mid": range_mid,
            "range_width_pct": range_width_pct,
            "range_compression_ratio": range_compression_ratio,
            "volume_ratio": volume_ratio,
            "atr_pct": _atr_pct_series(data, parameters["atr_window"]),
        }

    return pipeline


def _trend_pullback_breakout_feature_pipeline(parameters: dict[str, Any]) -> FeaturePipeline:
    def pipeline(data):
        closes = [float(item.close) for item in data]
        highs = [float(item.high) for item in data]
        lows = [float(item.low) for item in data]
        volumes = [float(item.volume) for item in data]
        fast = _ema_series(closes, parameters["fast_window"])
        slow = _ema_series(closes, parameters["slow_window"])
        breakout_high = _rolling_extreme_excluding_current(
            highs,
            parameters["breakout_window"],
            max,
        )
        pullback_low = _rolling_extreme_excluding_current(
            lows,
            parameters["pullback_window"],
            min,
        )

        pullback_depth_pct: list[float | None] = []
        trend_spread_pct: list[float | None] = []
        for fast_value, slow_value, low_value in zip(fast, slow, pullback_low):
            if fast_value is None or low_value is None or fast_value == 0.0:
                pullback_depth_pct.append(None)
            else:
                pullback_depth_pct.append(
                    max(0.0, (float(fast_value) - float(low_value)) / abs(float(fast_value)))
                )

            if fast_value is None or slow_value is None or slow_value == 0.0:
                trend_spread_pct.append(None)
            else:
                trend_spread_pct.append(
                    (float(fast_value) - float(slow_value)) / abs(float(slow_value))
                )

        volume_ma = _rolling_mean_excluding_current(volumes, parameters["volume_window"])
        volume_ratio: list[float | None] = []
        for volume, average_volume in zip(volumes, volume_ma):
            if average_volume is None or average_volume <= 0.0:
                volume_ratio.append(None)
                continue
            volume_ratio.append(volume / average_volume)

        features = {
            "close": closes,
            "fast_ema": fast,
            "slow_ema": slow,
            "breakout_high": breakout_high,
            "pullback_low": pullback_low,
            "pullback_depth_pct": pullback_depth_pct,
            "trend_spread_pct": trend_spread_pct,
            "volume_ratio": volume_ratio,
            "atr_pct": _atr_pct_series(data, parameters["atr_window"]),
        }
        features.update(_candidate_regime_context_features(data, parameters))
        return features

    return pipeline


def _chandelier_breakout_feature_pipeline(parameters: dict[str, Any]) -> FeaturePipeline:
    def pipeline(data):
        closes = [float(item.close) for item in data]
        highs = [float(item.high) for item in data]
        volumes = [float(item.volume) for item in data]
        atr = _atr_series(data, parameters["atr_window"])
        breakout_high = _rolling_extreme_excluding_current(
            highs,
            parameters["entry_window"],
            max,
        )
        trailing_high = _rolling_extreme_excluding_current(
            highs,
            parameters["exit_window"],
            max,
        )
        chandelier_stop: list[float | None] = []
        for high_value, atr_value in zip(trailing_high, atr):
            if high_value is None or atr_value is None:
                chandelier_stop.append(None)
                continue
            chandelier_stop.append(
                high_value - float(atr_value) * parameters["atr_multiplier"]
            )

        volume_ma = _rolling_mean_excluding_current(volumes, parameters["volume_window"])
        volume_ratio: list[float | None] = []
        for volume, average_volume in zip(volumes, volume_ma):
            if average_volume is None or average_volume <= 0.0:
                volume_ratio.append(None)
                continue
            volume_ratio.append(volume / average_volume)

        return {
            "close": closes,
            "breakout_high": breakout_high,
            "chandelier_stop": chandelier_stop,
            "volume_ratio": volume_ratio,
            "atr_pct": _atr_pct_series(data, parameters["atr_window"]),
        }

    return pipeline


def _rolling_vwap_reversion_feature_pipeline(parameters: dict[str, Any]) -> FeaturePipeline:
    def pipeline(data):
        closes = [float(item.close) for item in data]
        highs = [float(item.high) for item in data]
        lows = [float(item.low) for item in data]
        volumes = [float(item.volume) for item in data]
        typical_prices = [
            (high + low + close) / 3.0
            for high, low, close in zip(highs, lows, closes)
        ]
        rolling_vwap: list[float | None] = []
        for index in range(len(data)):
            if index < parameters["vwap_window"]:
                rolling_vwap.append(None)
                continue
            start = index - parameters["vwap_window"]
            volume_sample = volumes[start:index]
            volume_sum = sum(volume_sample)
            if volume_sum <= 0.0:
                rolling_vwap.append(None)
                continue
            price_volume_sum = sum(
                price * volume
                for price, volume in zip(typical_prices[start:index], volume_sample)
            )
            rolling_vwap.append(price_volume_sum / volume_sum)

        lower_band: list[float | None] = []
        for vwap in rolling_vwap:
            if vwap is None:
                lower_band.append(None)
                continue
            lower_band.append(vwap * (1.0 - parameters["entry_band_pct"]))

        volume_ma = _rolling_mean_excluding_current(volumes, parameters["volume_window"])
        volume_ratio: list[float | None] = []
        for volume, average_volume in zip(volumes, volume_ma):
            if average_volume is None or average_volume <= 0.0:
                volume_ratio.append(None)
                continue
            volume_ratio.append(volume / average_volume)

        features = {
            "close": closes,
            "rolling_vwap": rolling_vwap,
            "lower_band": lower_band,
            "volume_ratio": volume_ratio,
            "atr_pct": _atr_pct_series(data, parameters["atr_window"]),
        }
        features.update(_candidate_regime_context_features(data, parameters))
        return features

    return pipeline


def _bollinger_feature_pipeline(parameters: dict[str, Any]) -> FeaturePipeline:
    def pipeline(data):
        closes = [float(item.close) for item in data]
        mid = _rolling_mean_excluding_current(closes, parameters["window"])
        standard_deviation = _rolling_stddev_excluding_current(closes, parameters["window"])
        lower = []
        upper = []
        for mid_value, stddev_value in zip(mid, standard_deviation):
            if mid_value is None or stddev_value is None:
                lower.append(None)
                upper.append(None)
                continue
            lower.append(mid_value - parameters["stddev"] * stddev_value)
            upper.append(mid_value + parameters["stddev"] * stddev_value)
        return {
            "close": closes,
            "bollinger_mid": mid,
            "bollinger_lower": lower,
            "bollinger_upper": upper,
        }

    return pipeline


def _ema_series(values: list[float], window: int) -> list[float | None]:
    result: list[float | None] = []
    alpha = 2.0 / (window + 1.0)
    ema_value: float | None = None
    for index, value in enumerate(values):
        if index + 1 < window:
            result.append(None)
            continue
        if ema_value is None:
            ema_value = sum(values[index + 1 - window : index + 1]) / window
        else:
            ema_value = value * alpha + ema_value * (1.0 - alpha)
        result.append(ema_value)
    return result


def _ema_optional_series(values: list[float | None], window: int) -> list[float | None]:
    result: list[float | None] = []
    alpha = 2.0 / (window + 1.0)
    observed_values: list[float] = []
    ema_value: float | None = None
    for value in values:
        if value is None:
            result.append(None)
            continue
        current = float(value)
        observed_values.append(current)
        if len(observed_values) < window:
            result.append(None)
            continue
        if ema_value is None:
            ema_value = sum(observed_values[-window:]) / window
        else:
            ema_value = current * alpha + ema_value * (1.0 - alpha)
        result.append(ema_value)
    return result


def _atr_pct_series(data: list[Any], window: int) -> list[float | None]:
    true_ranges = []
    result: list[float | None] = []
    for index, item in enumerate(data):
        previous_close = float(data[index - 1].close) if index > 0 else float(item.close)
        high = float(item.high)
        low = float(item.low)
        true_ranges.append(
            max(high - low, abs(high - previous_close), abs(low - previous_close))
        )
        if index + 1 < window or float(item.close) == 0.0:
            result.append(None)
            continue
        atr = sum(true_ranges[index + 1 - window : index + 1]) / window
        result.append(atr / abs(float(item.close)))
    return result


def _atr_series(data: list[Any], window: int) -> list[float | None]:
    true_ranges = []
    result: list[float | None] = []
    for index, item in enumerate(data):
        previous_close = float(data[index - 1].close) if index > 0 else float(item.close)
        high = float(item.high)
        low = float(item.low)
        true_ranges.append(
            max(high - low, abs(high - previous_close), abs(low - previous_close))
        )
        if index + 1 < window:
            result.append(None)
            continue
        result.append(sum(true_ranges[index + 1 - window : index + 1]) / window)
    return result


def _return_volatility_pct_series(values: list[float], window: int) -> list[float | None]:
    returns = [0.0]
    for index in range(1, len(values)):
        previous = values[index - 1]
        returns.append(0.0 if previous == 0.0 else (values[index] - previous) / previous)

    result: list[float | None] = []
    for index in range(len(values)):
        if index + 1 < window:
            result.append(None)
            continue
        sample = returns[index + 1 - window : index + 1]
        result.append(_stddev(sample))
    return result


def _rolling_extreme_excluding_current(
    values: list[float],
    window: int,
    reducer: Callable[[list[float]], float],
) -> list[float | None]:
    result: list[float | None] = []
    for index in range(len(values)):
        if index < window:
            result.append(None)
            continue
        result.append(reducer(values[index - window : index]))
    return result


def _rolling_mean_excluding_current(values: list[float], window: int) -> list[float | None]:
    result: list[float | None] = []
    for index in range(len(values)):
        if index < window:
            result.append(None)
            continue
        sample = values[index - window : index]
        result.append(sum(sample) / window)
    return result


def _rolling_stddev_excluding_current(values: list[float], window: int) -> list[float | None]:
    result: list[float | None] = []
    for index in range(len(values)):
        if index < window:
            result.append(None)
            continue
        result.append(_stddev(values[index - window : index]))
    return result


def _rolling_mean_optional_series(values: list[float | None], window: int) -> list[float | None]:
    result: list[float | None] = []
    observed_values: list[float] = []
    for value in values:
        if value is None:
            result.append(None)
            continue
        observed_values.append(float(value))
        if len(observed_values) < window:
            result.append(None)
            continue
        sample = observed_values[-window:]
        result.append(sum(sample) / window)
    return result


def _rolling_mean_optional_excluding_current(
    values: list[float | None],
    window: int,
) -> list[float | None]:
    result: list[float | None] = []
    for index in range(len(values)):
        if index < window:
            result.append(None)
            continue
        sample = values[index - window : index]
        if any(value is None for value in sample):
            result.append(None)
            continue
        result.append(sum(float(value) for value in sample) / window)
    return result


def _rsi_series(values: list[float], window: int) -> list[float | None]:
    result: list[float | None] = []
    for index in range(len(values)):
        if index < window:
            result.append(None)
            continue
        gains = []
        losses = []
        for item_index in range(index - window + 1, index + 1):
            change = values[item_index] - values[item_index - 1]
            if change >= 0.0:
                gains.append(change)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(change))
        average_gain = sum(gains) / window
        average_loss = sum(losses) / window
        if average_gain == 0.0 and average_loss == 0.0:
            result.append(50.0)
        elif average_loss == 0.0:
            result.append(100.0)
        else:
            relative_strength = average_gain / average_loss
            result.append(100.0 - (100.0 / (1.0 + relative_strength)))
    return result


def _candidate_regime_context_features(
    data: list[Any],
    parameters: dict[str, Any],
) -> dict[str, list[Any]]:
    context = _candidate_regime_context_series(
        data,
        trend_window=_candidate_regime_trend_window(parameters),
        volatility_window=_candidate_regime_volatility_window(parameters),
        liquidity_window=_candidate_regime_liquidity_window(parameters),
    )
    return {
        "candidate_market_regime": [item.market_regime for item in context],
        "candidate_regime_direction": [item.direction for item in context],
        "candidate_volatility_state": [item.volatility_state for item in context],
        "candidate_regime_tradability": [item.tradability for item in context],
        "candidate_regime_trend_score": [item.trend_score for item in context],
        "candidate_regime_volatility_score": [item.volatility_score for item in context],
        "candidate_regime_liquidity_score": [item.liquidity_score for item in context],
        "candidate_regime_reason_codes": [list(item.reason_codes) for item in context],
    }


def _candidate_strategy_min_bars(parameters: dict[str, Any]) -> int:
    window_values = [
        int(value)
        for key, value in parameters.items()
        if key.endswith("_window") or key == "window"
        if isinstance(value, int) and not isinstance(value, bool)
    ]
    if not window_values:
        return 3
    dependent_windows = [
        ("slow_window", "signal_window"),
        ("k_window", "d_window"),
        ("bb_window", "squeeze_window"),
        ("breakout_window", "compression_window"),
    ]
    dependent_minimums = [
        int(parameters[first]) + int(parameters[second]) + 2
        for first, second in dependent_windows
        if first in parameters and second in parameters
    ]
    return max([max(window_values) + 2] + dependent_minimums)


def _candidate_regime_context_series(
    data: list[Any],
    *,
    trend_window: int,
    volatility_window: int,
    liquidity_window: int,
) -> list[CandidateRegimeContext]:
    closes = [float(item.close) for item in data]
    volumes = [float(item.volume) for item in data]
    atr_pct = _atr_pct_series(data, volatility_window)
    volume_average = _rolling_mean_excluding_current(volumes, liquidity_window)
    contexts: list[CandidateRegimeContext] = []
    for index, close in enumerate(closes):
        if index < trend_window or close == 0.0:
            contexts.append(
                CandidateRegimeContext(
                    market_regime="unknown",
                    direction="unknown",
                    volatility_state="unknown",
                    tradability="observe_only",
                    trend_score=0.0,
                    volatility_score=0.0,
                    liquidity_score=0.0,
                    reason_codes=("candidate_regime_context:unknown",),
                )
            )
            continue

        previous_close = closes[index - trend_window]
        trend_return = 0.0 if previous_close == 0.0 else (close - previous_close) / abs(previous_close)
        current_atr_pct = atr_pct[index]
        volatility_score = _bounded_ratio(0.0 if current_atr_pct is None else current_atr_pct, 0.08)
        average_volume = volume_average[index]
        liquidity_score = (
            0.0
            if average_volume is None or average_volume <= 0.0
            else _bounded_ratio(volumes[index], average_volume * 2.0)
        )
        trend_score = _bounded_ratio(abs(trend_return), 0.04)

        if abs(trend_return) >= 0.01:
            direction = "bullish" if trend_return > 0.0 else "bearish"
        else:
            direction = "neutral"

        volatility_state = _candidate_volatility_state(
            0.0 if current_atr_pct is None else current_atr_pct
        )
        if volatility_state == "extreme":
            tradability = "avoid"
            market_regime = "chaos"
            reason_codes = ("candidate_regime_context:chaos",)
        elif direction == "neutral":
            tradability = "tradable"
            market_regime = f"range_{volatility_state}_vol"
            reason_codes = ("candidate_regime_context:range",)
        else:
            tradability = "tradable"
            prefix = "uptrend" if direction == "bullish" else "downtrend"
            market_regime = f"{prefix}_{volatility_state}_vol"
            reason_codes = ("candidate_regime_context:trend",)

        contexts.append(
            CandidateRegimeContext(
                market_regime=market_regime,
                direction=direction,
                volatility_state=volatility_state,
                tradability=tradability,
                trend_score=trend_score,
                volatility_score=volatility_score,
                liquidity_score=liquidity_score,
                reason_codes=reason_codes,
            )
        )
    return contexts


def _regime_filter_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    direction_filter = _choice_param(
        parameters,
        "regime_direction_filter",
        "any",
        _REGIME_DIRECTIONS,
    )
    volatility_filter = _choice_param(
        parameters,
        "regime_volatility_filter",
        "any",
        _REGIME_VOLATILITY_FILTERS,
    )
    return {
        "use_regime_filter": _bool_param(parameters, "use_regime_filter", False),
        "allow_regime_fallback": _bool_param(parameters, "allow_regime_fallback", True),
        "require_regime_tradable": _bool_param(parameters, "require_regime_tradable", True),
        "regime_direction_filter": direction_filter,
        "regime_volatility_filter": volatility_filter,
    }


def _regime_filter_passes(
    features: dict[str, list[Any]],
    index: int,
    parameters: dict[str, Any],
) -> bool:
    if not parameters.get("use_regime_filter", False):
        return True
    required_keys = (
        "candidate_regime_direction",
        "candidate_volatility_state",
        "candidate_regime_tradability",
    )
    if any(key not in features for key in required_keys):
        return bool(parameters.get("allow_regime_fallback", True))

    direction = _feature_value(features, "candidate_regime_direction", index)
    volatility_state = _feature_value(features, "candidate_volatility_state", index)
    tradability = _feature_value(features, "candidate_regime_tradability", index)
    if direction is None or volatility_state is None or tradability is None:
        return bool(parameters.get("allow_regime_fallback", True))
    if direction == "unknown" or volatility_state == "unknown":
        return bool(parameters.get("allow_regime_fallback", True))
    if parameters.get("require_regime_tradable", True) and tradability != "tradable":
        return False
    return _direction_filter_passes(
        str(direction),
        str(parameters.get("regime_direction_filter", "any")),
    ) and _volatility_filter_passes(
        str(volatility_state),
        str(parameters.get("regime_volatility_filter", "any")),
    )


def _feature_value(features: dict[str, list[Any]], key: str, index: int) -> Any:
    values = features.get(key)
    if values is None or index < 0 or index >= len(values):
        return None
    return values[index]


def _direction_filter_passes(direction: str, direction_filter: str) -> bool:
    if direction_filter == "any":
        return True
    return direction == direction_filter


def _volatility_filter_passes(volatility_state: str, volatility_filter: str) -> bool:
    if volatility_filter == "any":
        return True
    if volatility_filter == "low_or_normal":
        return volatility_state in {"low", "normal"}
    if volatility_filter == "normal_or_high":
        return volatility_state in {"normal", "high"}
    if volatility_filter == "not_extreme":
        return volatility_state != "extreme"
    return volatility_state == volatility_filter


def _candidate_regime_trend_window(parameters: dict[str, Any]) -> int:
    for key in (
        "slow_window",
        "trend_window",
        "breakout_window",
        "vwap_window",
        "channel_window",
        "entry_window",
        "window",
    ):
        if key in parameters:
            return max(2, int(parameters[key]))
    return 20


def _candidate_regime_volatility_window(parameters: dict[str, Any]) -> int:
    if "atr_window" in parameters:
        return max(1, int(parameters["atr_window"]))
    if "volatility_window" in parameters:
        return max(2, int(parameters["volatility_window"]))
    return 14


def _candidate_regime_liquidity_window(parameters: dict[str, Any]) -> int:
    if "volume_window" in parameters:
        return max(2, int(parameters["volume_window"]))
    return 20


def _candidate_volatility_state(atr_pct: float) -> str:
    if atr_pct >= 0.08:
        return "extreme"
    if atr_pct >= 0.04:
        return "high"
    if atr_pct <= 0.006:
        return "low"
    return "normal"


def _bounded_ratio(value: float, denominator: float) -> float:
    if denominator <= 0.0:
        return 0.0
    return min(1.0, max(0.0, float(value) / float(denominator)))


def _stddev(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance)


def _normalize_strategy_id(strategy_id: str) -> str:
    normalized = str(strategy_id).strip().lower()
    if not normalized:
        raise ValueError("unsupported_candidate_strategy: empty strategy_id")
    if normalized not in SUPPORTED_CANDIDATE_STRATEGY_IDS:
        raise ValueError(f"unsupported_candidate_strategy: {strategy_id}")
    return normalized


def _reject_unknown_parameters(parameters: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(parameters) - allowed)
    if unknown:
        raise ValueError(
            "invalid_candidate_strategy_parameters: unsupported parameters "
            + ", ".join(unknown)
        )


def _int_param(
    parameters: dict[str, Any],
    name: str,
    default: int,
    *,
    minimum: int,
) -> int:
    value = parameters.get(name, default)
    if isinstance(value, bool):
        raise ValueError(f"invalid_candidate_strategy_parameters: {name} must be an integer")
    try:
        integer_value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"invalid_candidate_strategy_parameters: {name} must be an integer"
        ) from exc
    if integer_value < minimum:
        raise ValueError(
            f"invalid_candidate_strategy_parameters: {name} must be >= {minimum}"
        )
    return integer_value


def _float_param(
    parameters: dict[str, Any],
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float | None = None,
) -> float:
    value = parameters.get(name, default)
    if isinstance(value, bool):
        raise ValueError(f"invalid_candidate_strategy_parameters: {name} must be numeric")
    try:
        float_value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"invalid_candidate_strategy_parameters: {name} must be numeric"
        ) from exc
    if float_value < minimum:
        raise ValueError(
            f"invalid_candidate_strategy_parameters: {name} must be >= {minimum}"
        )
    if maximum is not None and float_value > maximum:
        raise ValueError(
            f"invalid_candidate_strategy_parameters: {name} must be <= {maximum}"
        )
    return float_value


def _bool_param(parameters: dict[str, Any], name: str, default: bool) -> bool:
    value = parameters.get(name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y"}:
            return True
        if normalized in {"0", "false", "no", "n"}:
            return False
    raise ValueError(f"invalid_candidate_strategy_parameters: {name} must be boolean")


def _choice_param(
    parameters: dict[str, Any],
    name: str,
    default: str,
    allowed: set[str],
) -> str:
    value = str(parameters.get(name, default)).strip().lower()
    if value not in allowed:
        raise ValueError(
            f"invalid_candidate_strategy_parameters: {name} must be one of "
            + ", ".join(sorted(allowed))
        )
    return value
