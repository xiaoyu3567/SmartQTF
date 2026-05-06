import json
import math
from pathlib import Path

from quant.optimization.candidate_strategies import (
    SUPPORTED_CANDIDATE_STRATEGY_IDS,
    candidate_regime_context_contract,
    candidate_strategy_metadata,
    create_candidate_strategy,
)
from quant.optimization.source_report_generation import (
    HistoricalValidationWindowConfig,
    generate_oos_source_report,
)
from quant.strategy.stateless import validate_stateless_strategy


def _write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _build_trending_klines(count=240):
    pattern = [100.0, 104.0, 99.0, 106.0, 98.0, 108.0, 97.0, 110.0]
    closes = [pattern[index % len(pattern)] + index * 0.02 for index in range(count)]
    rows = []
    for index, close in enumerate(closes):
        previous = closes[index - 1] if index > 0 else close
        rows.append(
            {
                "timestamp": 1702000000 + index * 60,
                "open": previous,
                "high": max(previous, close) + 1.2,
                "low": min(previous, close) - 1.2,
                "close": close,
                "volume": 1000.0 + index,
            }
        )
    return rows


def _build_wave_klines(count=320):
    closes = []
    for index in range(count):
        closes.append(100.0 + 4.5 * math.sin(index / 4.0) + 2.2 * math.sin(index / 1.6))
    rows = []
    for index, close in enumerate(closes):
        previous = closes[index - 1] if index > 0 else close
        rows.append(
            {
                "timestamp": 1703000000 + index * 60,
                "open": previous,
                "high": max(previous, close) + 1.0,
                "low": min(previous, close) - 1.0,
                "close": close,
                "volume": 2000.0 + index,
            }
        )
    return rows


def _build_gap_reversal_klines(count=320):
    rows = []
    previous_close = 100.0
    for index in range(count):
        if index % 18 == 6:
            open_price = previous_close * 0.992
            close = previous_close * 1.001
            volume = 4200.0 + index
        elif index % 18 == 7:
            open_price = previous_close * 1.003
            close = previous_close * 0.997
            volume = 2600.0 + index
        else:
            close = 100.0 + 1.4 * math.sin(index / 3.2) + 0.01 * index
            open_price = previous_close
            volume = 1800.0 + index
        high = max(open_price, close) + 0.8
        low = min(open_price, close) - 0.8
        rows.append(
            {
                "timestamp": 1704000000 + index * 60,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
        previous_close = close
    return rows


def _build_gap_continuation_breakout_klines(count=360):
    rows = []
    previous_close = 100.0
    for index in range(count):
        if index % 20 == 8:
            open_price = previous_close * 1.006
            close = open_price * 1.006
            volume = 4400.0 + index
        elif index % 20 in {9, 10, 11}:
            open_price = previous_close
            close = previous_close * 1.003
            volume = 2600.0 + index
        elif index % 20 == 14:
            open_price = previous_close * 0.996
            close = open_price * 0.996
            volume = 2600.0 + index
        else:
            open_price = previous_close
            close = previous_close * (1.0 + 0.0006 * math.sin(index / 3.0))
            volume = 1800.0 + index
        high = max(open_price, close) + 0.35
        low = min(open_price, close) - 0.35
        rows.append(
            {
                "timestamp": 1704500000 + index * 60,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
        previous_close = close
    return rows


def _build_squeeze_breakout_klines(count=360):
    rows = []
    closes = []
    for index in range(count):
        if index % 24 < 18:
            close = 100.0 + 0.12 * math.sin(index / 2.0)
        elif index % 24 == 18:
            close = 101.5 + 0.01 * index
        else:
            close = 101.5 + 0.01 * index + 0.4 * math.sin(index / 2.5)
        closes.append(close)

    for index, close in enumerate(closes):
        previous = closes[index - 1] if index > 0 else close
        high = max(previous, close) + 0.18
        low = min(previous, close) - 0.18
        volume = 1800.0 + index
        if index % 24 == 18:
            volume = 4200.0 + index
        rows.append(
            {
                "timestamp": 1705000000 + index * 60,
                "open": previous,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
    return rows


def _build_liquidity_sweep_klines(count=360):
    rows = []
    previous_close = 100.0
    for index in range(count):
        close = 100.0 + 1.8 * math.sin(index / 4.0) + 0.015 * index
        open_price = previous_close
        high = max(open_price, close) + 0.7
        low = min(open_price, close) - 0.7
        volume = 1800.0 + index
        if index % 24 == 12:
            range_floor = min(row["low"] for row in rows[-10:]) if len(rows) >= 10 else low
            low = range_floor * 0.994
            close = max(open_price, range_floor * 1.004) + 0.4
            high = max(open_price, close) + 0.8
            volume = 4300.0 + index
        rows.append(
            {
                "timestamp": 1706000000 + index * 60,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
        previous_close = close
    return rows


def _build_range_compression_breakout_klines(count=360):
    rows = []
    closes = []
    for index in range(count):
        cycle_index = index % 24
        if cycle_index < 18:
            close = 100.0 + 0.18 * math.sin(index / 2.0)
        elif cycle_index == 18:
            close = 101.6 + 0.01 * index
        else:
            close = 101.6 + 0.01 * index + 0.35 * math.sin(index / 2.2)
        closes.append(close)

    for index, close in enumerate(closes):
        previous = closes[index - 1] if index > 0 else close
        if index % 24 < 18:
            high = max(previous, close) + 0.12
            low = min(previous, close) - 0.12
        else:
            high = max(previous, close) + 0.45
            low = min(previous, close) - 0.25
        volume = 1700.0 + index
        if index % 24 == 18:
            volume = 4300.0 + index
        rows.append(
            {
                "timestamp": 1707000000 + index * 60,
                "open": previous,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
    return rows


def _build_trend_pullback_breakout_klines(count=420):
    rows = []
    closes = []
    for index in range(count):
        cycle_index = index % 30
        trend = 100.0 + 0.05 * index
        if cycle_index < 16:
            close = trend + 0.18 * math.sin(index / 2.4)
        elif cycle_index < 24:
            close = trend - 1.3 + 0.12 * math.sin(index / 1.8)
        elif cycle_index == 24:
            close = trend + 1.6
        else:
            close = trend + 1.6 + 0.2 * math.sin(index / 2.2)
        closes.append(close)

    for index, close in enumerate(closes):
        previous = closes[index - 1] if index > 0 else close
        high = max(previous, close) + 0.45
        low = min(previous, close) - 0.45
        volume = 1900.0 + index
        if index % 30 == 24:
            volume = 4300.0 + index
        rows.append(
            {
                "timestamp": 1708000000 + index * 60,
                "open": previous,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
    return rows


def _build_chandelier_breakout_klines(count=420):
    rows = []
    closes = []
    for index in range(count):
        cycle_index = index % 28
        base = 100.0 + 0.025 * index
        if cycle_index < 18:
            close = base + 0.22 * math.sin(index / 2.5)
        elif cycle_index == 18:
            close = base + 1.8
        elif cycle_index < 23:
            close = base + 1.8 + 0.25 * math.sin(index / 2.0)
        elif cycle_index == 23:
            close = base + 0.25
        else:
            close = base + 0.45 * math.sin(index / 2.3)
        closes.append(close)

    for index, close in enumerate(closes):
        previous = closes[index - 1] if index > 0 else close
        high = max(previous, close) + 0.35
        low = min(previous, close) - 0.35
        volume = 1800.0 + index
        if index % 28 == 18:
            volume = 4300.0 + index
        rows.append(
            {
                "timestamp": 1709000000 + index * 60,
                "open": previous,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
    return rows


def _build_rolling_vwap_reversion_klines(count=420):
    rows = []
    closes = []
    for index in range(count):
        cycle_index = index % 28
        base = 100.0 + 1.4 * math.sin(index / 5.0)
        if cycle_index == 18:
            close = base - 2.8
        elif cycle_index in {19, 20, 21}:
            close = base - 1.2 + 0.45 * (cycle_index - 19)
        elif cycle_index == 22:
            close = base + 0.6
        else:
            close = base + 0.35 * math.sin(index / 2.0)
        closes.append(close)

    for index, close in enumerate(closes):
        previous = closes[index - 1] if index > 0 else close
        high = max(previous, close) + 0.32
        low = min(previous, close) - 0.32
        volume = 1800.0 + index
        if index % 28 == 18:
            volume = 4300.0 + index
        rows.append(
            {
                "timestamp": 1710000000 + index * 60,
                "open": previous,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
    return rows


def test_supported_candidate_strategy_ids_cover_h_strat_010_scope():
    assert {
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
    }.issubset(set(SUPPORTED_CANDIDATE_STRATEGY_IDS))


def test_candidate_strategy_pool_stateless_contract():
    feature_payloads = {
        "ma_crossover": {"fast_ma": [None, 1.0, 3.0], "slow_ma": [None, 2.0, 2.0]},
        "ema_trend_filter": {
            "fast_ema": [None, 1.0, 3.0],
            "slow_ema": [None, 2.0, 2.0],
            "atr_pct": [None, 0.01, 0.01],
            "return_volatility_pct": [None, 0.01, 0.01],
            "trend_strength": [None, 0.2, 0.2],
        },
        "donchian_breakout": {
            "close": [1.0, 2.0, 3.0],
            "donchian_high": [None, None, 2.0],
            "donchian_low": [None, None, 1.0],
            "exit_low": [None, None, 1.0],
        },
        "keltner_breakout": {
            "close": [1.0, 1.0, 3.0],
            "keltner_mid": [None, 1.0, 1.5],
            "keltner_lower": [None, 0.5, 1.0],
            "keltner_upper": [None, 1.2, 2.0],
            "atr": [None, 0.5, 0.5],
        },
        "volume_breakout": {
            "close": [1.0, 1.0, 3.0],
            "volume": [100.0, 100.0, 200.0],
            "breakout_high": [None, 1.2, 2.0],
            "exit_low": [None, 0.8, 0.9],
            "volume_ma": [None, 100.0, 100.0],
            "volume_ratio": [None, 1.0, 2.0],
        },
        "macd_momentum": {
            "close": [1.0, 1.0, 3.0],
            "macd_line": [None, 0.1, 0.3],
            "macd_signal": [None, 0.2, 0.2],
            "macd_histogram": [None, -0.1, 0.1],
            "macd_histogram_pct": [None, -0.1, 0.04],
            "atr_pct": [None, 0.01, 0.01],
        },
        "roc_momentum": {
            "close": [1.0, 1.0, 3.0],
            "roc_pct": [None, 0.0, 0.2],
            "trend_ema": [None, 1.1, 2.0],
            "atr_pct": [None, 0.01, 0.01],
        },
        "stochastic_reversion": {
            "close": [1.0, 1.0, 1.0],
            "stochastic_k": [None, 10.0, 30.0],
            "stochastic_d": [None, 15.0, 25.0],
        },
        "ema_pullback_reentry": {
            "close": [1.0, 1.0, 3.0],
            "fast_ema": [None, 1.2, 2.0],
            "slow_ema": [None, 1.0, 1.5],
            "rsi": [None, 35.0, 55.0],
            "atr_pct": [None, 0.01, 0.01],
        },
        "atr_channel_reversion": {
            "close": [1.0, 0.8, 1.2],
            "channel_mid": [None, 1.0, 1.0],
            "channel_lower": [None, 0.9, 0.9],
            "channel_upper": [None, 1.3, 1.3],
            "atr": [None, 0.2, 0.2],
            "atr_pct": [None, 0.02, 0.02],
        },
        "gap_reversal": {
            "open": [1.0, 0.98, 0.94],
            "close": [1.0, 1.0, 0.99],
            "previous_close": [None, 1.0, 1.0],
            "gap_pct": [None, -0.02, -0.06],
            "gap_reclaim_ratio": [None, 1.0, 0.8333333333333334],
            "volume_ratio": [None, 1.2, 1.4],
            "atr_pct": [None, 0.01, 0.01],
        },
        "gap_continuation_breakout": {
            "open": [1.0, 1.02, 1.0],
            "close": [1.0, 1.04, 0.97],
            "previous_close": [None, 1.0, 1.04],
            "gap_pct": [None, 0.02, -0.038461538461538464],
            "gap_follow_through_ratio": [None, 1.0, -0.75],
            "volume_ratio": [None, 1.2, 1.4],
            "atr_pct": [None, 0.01, 0.01],
        },
        "liquidity_sweep_reversal": {
            "low": [1.0, 0.96, 0.94],
            "high": [1.2, 1.2, 1.2],
            "close": [1.1, 1.1, 1.08],
            "range_low": [None, 1.0, 0.98],
            "range_high": [None, 1.2, 1.2],
            "close_position": [None, 0.6, 0.9285714285714285],
            "volume_ratio": [None, 1.1, 1.4],
            "atr_pct": [None, 0.01, 0.01],
        },
        "volatility_squeeze_breakout": {
            "close": [1.0, 1.0, 1.4],
            "breakout_high": [None, 1.2, 1.2],
            "squeeze_mid": [None, 1.0, 1.0],
            "bandwidth_pct": [None, 0.02, 0.02],
            "squeeze_ratio": [None, 0.6, 0.6],
            "volume_ratio": [None, 1.0, 1.3],
            "atr_pct": [None, 0.01, 0.01],
        },
        "range_compression_breakout": {
            "close": [1.0, 1.0, 1.4],
            "range_high": [None, 1.2, 1.2],
            "range_low": [None, 0.9, 0.9],
            "range_mid": [None, 1.05, 1.05],
            "range_width_pct": [None, 0.01, 0.01],
            "range_compression_ratio": [None, 0.6, 0.6],
            "volume_ratio": [None, 1.0, 1.3],
            "atr_pct": [None, 0.01, 0.01],
        },
        "trend_pullback_breakout": {
            "close": [1.0, 1.0, 1.4],
            "fast_ema": [None, 1.1, 1.2],
            "slow_ema": [None, 1.0, 1.0],
            "breakout_high": [None, 1.2, 1.2],
            "pullback_low": [None, 0.98, 0.98],
            "pullback_depth_pct": [None, 0.04, 0.04],
            "trend_spread_pct": [None, 0.1, 0.2],
            "volume_ratio": [None, 1.0, 1.3],
            "atr_pct": [None, 0.01, 0.01],
        },
        "chandelier_breakout": {
            "close": [1.0, 1.0, 1.4],
            "breakout_high": [None, 1.2, 1.2],
            "chandelier_stop": [None, 0.8, 0.9],
            "volume_ratio": [None, 1.0, 1.3],
            "atr_pct": [None, 0.01, 0.01],
        },
        "rolling_vwap_reversion": {
            "close": [1.0, 1.0, 0.94],
            "rolling_vwap": [None, 1.0, 1.0],
            "lower_band": [None, 0.97, 0.97],
            "volume_ratio": [None, 1.0, 1.3],
            "atr_pct": [None, 0.01, 0.01],
        },
        "rsi_mean_reversion": {
            "close": [1.0, 1.0, 1.0],
            "rsi": [None, 40.0, 50.0],
        },
        "bollinger_reversion": {
            "close": [1.0, 1.0, 1.0],
            "bollinger_mid": [None, 1.0, 1.0],
            "bollinger_lower": [None, 0.9, 0.8],
            "bollinger_upper": [None, 1.1, 1.2],
        },
    }
    params = {
        "ma_crossover": {"fast_window": 2, "slow_window": 6},
        "ema_trend_filter": {"fast_window": 3, "slow_window": 8},
        "donchian_breakout": {"channel_window": 4, "exit_window": 3},
        "keltner_breakout": {
            "ema_window": 3,
            "atr_window": 3,
            "atr_multiplier": 1.2,
            "exit_midline": True,
        },
        "volume_breakout": {
            "price_window": 3,
            "volume_window": 3,
            "min_volume_ratio": 1.2,
            "exit_window": 2,
            "exit_on_breakdown": True,
        },
        "macd_momentum": {
            "fast_window": 3,
            "slow_window": 8,
            "signal_window": 3,
            "atr_window": 3,
            "min_histogram_pct": 0.0,
            "max_atr_pct": 0.2,
            "exit_on_signal_cross": True,
        },
        "roc_momentum": {
            "roc_window": 3,
            "trend_window": 5,
            "atr_window": 3,
            "min_roc_pct": 0.05,
            "max_atr_pct": 0.2,
            "exit_roc_pct": 0.0,
            "exit_on_trend_loss": True,
        },
        "stochastic_reversion": {
            "k_window": 8,
            "d_window": 3,
            "oversold": 20.0,
            "overbought": 75.0,
            "exit_k": 50.0,
            "exit_on_midline": True,
        },
        "ema_pullback_reentry": {
            "fast_window": 3,
            "slow_window": 8,
            "rsi_window": 3,
            "pullback_rsi": 40.0,
            "reentry_rsi": 52.0,
            "exit_rsi": 70.0,
            "atr_window": 3,
            "max_atr_pct": 0.2,
            "exit_on_trend_loss": True,
        },
        "atr_channel_reversion": {
            "ema_window": 3,
            "atr_window": 3,
            "atr_multiplier": 1.0,
            "min_atr_pct": 0.0,
            "max_atr_pct": 0.2,
            "exit_midline": True,
        },
        "gap_reversal": {
            "atr_window": 3,
            "volume_window": 3,
            "min_gap_pct": 0.01,
            "min_reclaim_ratio": 0.5,
            "min_volume_ratio": 1.0,
            "max_atr_pct": 0.2,
            "exit_on_up_gap": True,
        },
        "gap_continuation_breakout": {
            "atr_window": 3,
            "volume_window": 3,
            "min_gap_pct": 0.01,
            "min_follow_through_ratio": 0.5,
            "min_volume_ratio": 1.0,
            "min_atr_pct": 0.0,
            "max_atr_pct": 0.2,
            "exit_on_down_gap": True,
        },
        "liquidity_sweep_reversal": {
            "range_window": 3,
            "atr_window": 3,
            "volume_window": 3,
            "min_sweep_pct": 0.005,
            "min_close_position": 0.6,
            "min_volume_ratio": 1.0,
            "min_atr_pct": 0.0,
            "max_atr_pct": 0.2,
            "exit_on_bearish_sweep": True,
        },
        "volatility_squeeze_breakout": {
            "breakout_window": 3,
            "bb_window": 3,
            "squeeze_window": 3,
            "bandwidth_stddev": 1.5,
            "max_squeeze_ratio": 0.8,
            "min_volume_ratio": 1.0,
            "atr_window": 3,
            "min_atr_pct": 0.0,
            "max_atr_pct": 0.2,
            "exit_on_midline_loss": True,
        },
        "range_compression_breakout": {
            "breakout_window": 3,
            "compression_window": 3,
            "volume_window": 3,
            "atr_window": 3,
            "max_range_width_pct": 0.02,
            "max_compression_ratio": 0.8,
            "min_volume_ratio": 1.0,
            "min_atr_pct": 0.0,
            "max_atr_pct": 0.2,
            "exit_on_midline_loss": True,
        },
        "trend_pullback_breakout": {
            "fast_window": 3,
            "slow_window": 8,
            "breakout_window": 3,
            "pullback_window": 3,
            "volume_window": 3,
            "atr_window": 3,
            "min_pullback_depth_pct": 0.01,
            "max_pullback_depth_pct": 0.08,
            "min_trend_spread_pct": 0.0,
            "min_volume_ratio": 1.0,
            "min_atr_pct": 0.0,
            "max_atr_pct": 0.2,
            "exit_on_fast_ema_loss": True,
        },
        "chandelier_breakout": {
            "entry_window": 3,
            "exit_window": 3,
            "atr_window": 3,
            "atr_multiplier": 1.5,
            "volume_window": 3,
            "min_volume_ratio": 1.0,
            "min_atr_pct": 0.0,
            "max_atr_pct": 0.2,
            "exit_on_chandelier_loss": True,
        },
        "rolling_vwap_reversion": {
            "vwap_window": 3,
            "volume_window": 3,
            "atr_window": 3,
            "entry_band_pct": 0.02,
            "min_volume_ratio": 1.0,
            "min_atr_pct": 0.0,
            "max_atr_pct": 0.2,
            "exit_on_vwap_reclaim": True,
        },
        "rsi_mean_reversion": {"rsi_window": 8, "oversold": 45, "overbought": 65, "exit_rsi": 52},
        "bollinger_reversion": {"window": 10, "stddev": 1.6, "exit_midline": True},
    }

    for strategy_id in SUPPORTED_CANDIDATE_STRATEGY_IDS:
        strategy = create_candidate_strategy(strategy_id, params.get(strategy_id)).strategy
        result = validate_stateless_strategy(strategy, feature_payloads[strategy_id], 2)
        assert result.passed, f"{strategy_id}: {result.errors}"


def test_regime_aware_candidate_metadata_is_replayable_and_cross_symbol():
    regime_contract = candidate_regime_context_contract()
    regime_aware_ids = {
        "ema_trend_filter",
        "trend_pullback_breakout",
        "rolling_vwap_reversion",
    }

    for strategy_id in SUPPORTED_CANDIDATE_STRATEGY_IDS:
        spec = create_candidate_strategy(strategy_id)
        metadata = spec.strategy_metadata

        assert metadata == candidate_strategy_metadata(strategy_id, spec.parameters)
        assert metadata["metadata_contract_version"] == "1.0"
        assert metadata["strategy_id"] == strategy_id
        assert metadata["cross_symbol"] is True
        assert metadata["symbol_agnostic_ohlcv_input"] is True
        assert metadata["deterministic"] is True
        assert metadata["stateless_delayed_signal"] is True
        assert metadata["does_not_call_broker_risk_execution_or_portfolio"] is True
        assert metadata["does_not_send_orders"] is True
        assert metadata["does_not_modify_live_state"] is True
        assert metadata["min_bars"] >= 3
        assert metadata["parameter_space"]["grid_enumerable"] is True
        assert set(spec.parameters).issubset(
            set(metadata["parameter_space"]["parameter_keys"])
        )
        assert metadata["reason_codes"]["entry"]
        assert "candidate_strategy_metadata_v1" in metadata["reason_codes"]["metadata"]

    for strategy_id in regime_aware_ids:
        metadata = create_candidate_strategy(strategy_id).strategy_metadata
        assert metadata["regime_aware"] is True
        assert metadata["regime_context_contract"] == regime_contract
        assert metadata["regime_context_contract_version"] == "1.0"
        assert set(regime_contract["feature_keys"]).issubset(
            set(metadata["feature_requirements"])
        )


def test_candidate_strategy_pool_generates_replayable_oos_reports(tmp_path):
    trending_path = _write_json(tmp_path / "inputs" / "trend.json", {"klines": _build_trending_klines()})
    wave_path = _write_json(tmp_path / "inputs" / "wave.json", {"klines": _build_wave_klines()})
    gap_path = _write_json(tmp_path / "inputs" / "gap.json", {"klines": _build_gap_reversal_klines()})
    gap_continuation_path = _write_json(
        tmp_path / "inputs" / "gap-continuation.json",
        {"klines": _build_gap_continuation_breakout_klines()},
    )
    squeeze_path = _write_json(
        tmp_path / "inputs" / "squeeze.json",
        {"klines": _build_squeeze_breakout_klines()},
    )
    sweep_path = _write_json(
        tmp_path / "inputs" / "liquidity-sweep.json",
        {"klines": _build_liquidity_sweep_klines()},
    )
    compression_path = _write_json(
        tmp_path / "inputs" / "range-compression.json",
        {"klines": _build_range_compression_breakout_klines()},
    )
    trend_pullback_path = _write_json(
        tmp_path / "inputs" / "trend-pullback.json",
        {"klines": _build_trend_pullback_breakout_klines()},
    )
    chandelier_path = _write_json(
        tmp_path / "inputs" / "chandelier.json",
        {"klines": _build_chandelier_breakout_klines()},
    )
    rolling_vwap_path = _write_json(
        tmp_path / "inputs" / "rolling-vwap.json",
        {"klines": _build_rolling_vwap_reversion_klines()},
    )
    output_dir = tmp_path / "source-reports"

    cases = [
        (
            "ema_trend_filter",
            trending_path,
            {
                "fast_window": 3,
                "slow_window": 8,
                "atr_window": 5,
                "volatility_window": 8,
                "max_atr_pct": 0.2,
                "max_volatility_pct": 0.2,
                "min_trend_strength": 0.0,
            },
            60,
            40,
            0.4,
        ),
        (
            "donchian_breakout",
            trending_path,
            {"channel_window": 4, "exit_window": 3},
            60,
            40,
            0.4,
        ),
        (
            "keltner_breakout",
            trending_path,
            {
                "ema_window": 3,
                "atr_window": 3,
                "atr_multiplier": 0.2,
                "exit_midline": True,
            },
            60,
            40,
            0.4,
        ),
        (
            "volume_breakout",
            trending_path,
            {
                "price_window": 3,
                "volume_window": 3,
                "min_volume_ratio": 0.8,
                "exit_window": 2,
                "exit_on_breakdown": True,
            },
            60,
            40,
            0.4,
        ),
        (
            "macd_momentum",
            trending_path,
            {
                "fast_window": 3,
                "slow_window": 8,
                "signal_window": 3,
                "atr_window": 3,
                "min_histogram_pct": 0.0,
                "max_atr_pct": 0.2,
                "exit_on_signal_cross": True,
            },
            60,
            40,
            0.4,
        ),
        (
            "roc_momentum",
            trending_path,
            {
                "roc_window": 3,
                "trend_window": 5,
                "atr_window": 3,
                "min_roc_pct": 0.01,
                "max_atr_pct": 0.2,
                "exit_roc_pct": 0.0,
                "exit_on_trend_loss": True,
            },
            60,
            40,
            0.4,
        ),
        (
            "rsi_mean_reversion",
            wave_path,
            {"rsi_window": 8, "oversold": 45, "overbought": 65, "exit_rsi": 52},
            80,
            60,
            0.35,
        ),
        (
            "stochastic_reversion",
            wave_path,
            {
                "k_window": 8,
                "d_window": 3,
                "oversold": 25.0,
                "overbought": 75.0,
                "exit_k": 50.0,
                "exit_on_midline": True,
            },
            80,
            60,
            0.35,
        ),
        (
            "ema_pullback_reentry",
            trending_path,
            {
                "fast_window": 3,
                "slow_window": 8,
                "rsi_window": 3,
                "pullback_rsi": 45.0,
                "reentry_rsi": 52.0,
                "exit_rsi": 70.0,
                "atr_window": 3,
                "max_atr_pct": 0.2,
                "exit_on_trend_loss": True,
            },
            80,
            60,
            0.35,
        ),
        (
            "atr_channel_reversion",
            wave_path,
            {
                "ema_window": 3,
                "atr_window": 3,
                "atr_multiplier": 0.2,
                "min_atr_pct": 0.0,
                "max_atr_pct": 0.5,
                "exit_midline": True,
            },
            80,
            60,
            0.35,
        ),
        (
            "gap_reversal",
            gap_path,
            {
                "atr_window": 3,
                "volume_window": 4,
                "min_gap_pct": 0.004,
                "min_reclaim_ratio": 0.6,
                "min_volume_ratio": 1.0,
                "max_atr_pct": 0.2,
                "exit_on_up_gap": True,
            },
            80,
            60,
            0.35,
        ),
        (
            "gap_continuation_breakout",
            gap_continuation_path,
            {
                "atr_window": 3,
                "volume_window": 4,
                "min_gap_pct": 0.003,
                "min_follow_through_ratio": 0.3,
                "min_volume_ratio": 0.8,
                "min_atr_pct": 0.0,
                "max_atr_pct": 0.2,
                "exit_on_down_gap": True,
            },
            90,
            70,
            0.35,
        ),
        (
            "volatility_squeeze_breakout",
            squeeze_path,
            {
                "breakout_window": 5,
                "bb_window": 5,
                "squeeze_window": 5,
                "bandwidth_stddev": 1.5,
                "max_squeeze_ratio": 1.1,
                "min_volume_ratio": 1.0,
                "atr_window": 5,
                "min_atr_pct": 0.0,
                "max_atr_pct": 0.2,
                "exit_on_midline_loss": True,
            },
            100,
            80,
            0.35,
        ),
        (
            "liquidity_sweep_reversal",
            sweep_path,
            {
                "range_window": 10,
                "atr_window": 5,
                "volume_window": 5,
                "min_sweep_pct": 0.002,
                "min_close_position": 0.55,
                "min_volume_ratio": 0.8,
                "min_atr_pct": 0.0,
                "max_atr_pct": 0.2,
                "exit_on_bearish_sweep": True,
            },
            100,
            80,
            0.35,
        ),
        (
            "range_compression_breakout",
            compression_path,
            {
                "breakout_window": 8,
                "compression_window": 5,
                "volume_window": 5,
                "atr_window": 5,
                "max_range_width_pct": 0.03,
                "max_compression_ratio": 1.1,
                "min_volume_ratio": 0.8,
                "min_atr_pct": 0.0,
                "max_atr_pct": 0.2,
                "exit_on_midline_loss": True,
            },
            100,
            80,
            0.35,
        ),
        (
            "trend_pullback_breakout",
            trend_pullback_path,
            {
                "fast_window": 5,
                "slow_window": 13,
                "breakout_window": 8,
                "pullback_window": 8,
                "volume_window": 5,
                "atr_window": 5,
                "min_pullback_depth_pct": 0.005,
                "max_pullback_depth_pct": 0.08,
                "min_trend_spread_pct": 0.0,
                "min_volume_ratio": 0.8,
                "min_atr_pct": 0.0,
                "max_atr_pct": 0.2,
                "exit_on_fast_ema_loss": True,
            },
            120,
            90,
            0.35,
        ),
        (
            "chandelier_breakout",
            chandelier_path,
            {
                "entry_window": 8,
                "exit_window": 8,
                "atr_window": 5,
                "atr_multiplier": 1.5,
                "volume_window": 5,
                "min_volume_ratio": 0.8,
                "min_atr_pct": 0.0,
                "max_atr_pct": 0.2,
                "exit_on_chandelier_loss": True,
            },
            120,
            90,
            0.35,
        ),
        (
            "rolling_vwap_reversion",
            rolling_vwap_path,
            {
                "vwap_window": 8,
                "volume_window": 5,
                "atr_window": 5,
                "entry_band_pct": 0.006,
                "min_volume_ratio": 0.8,
                "min_atr_pct": 0.0,
                "max_atr_pct": 0.2,
                "exit_on_vwap_reclaim": True,
            },
            120,
            90,
            0.35,
        ),
    ]

    for strategy_id, source_path, strategy_parameters, min_train_bars, min_holdout_bars, holdout_ratio in cases:
        config = HistoricalValidationWindowConfig(
            source_path=str(source_path),
            strategy_id=strategy_id,
            candidate_version=f"review-2026-05-04-BTCUSDT-{strategy_id}",
            symbol="BTCUSDT",
            timeframe="1m",
            output_dir=str(output_dir),
            min_train_bars=min_train_bars,
            min_holdout_bars=min_holdout_bars,
            min_trades=1,
            holdout_ratio=holdout_ratio,
            strategy_parameters=strategy_parameters,
            overwrite_existing=True,
            generated_at=1777827600,
        )
        result = generate_oos_source_report(config)

        assert result.status == "PASS"
        assert result.reason_codes == []
        assert result.metrics["trade_count"] >= 1
        assert result.source_report_path is not None

        payload = json.loads(Path(result.source_report_path).read_text(encoding="utf-8"))
        assert payload["strategy_id"] == strategy_id
        assert payload["provenance"]["strategy_id"] == strategy_id
        assert dict(strategy_parameters).items() <= payload["provenance"][
            "strategy_parameters"
        ].items()
        assert payload["provenance"]["strategy_metadata"]["strategy_id"] == strategy_id
        assert payload["provenance"]["strategy_metadata"]["cross_symbol"] is True
        assert payload["provenance"]["strategy_metadata"]["stateless_delayed_signal"] is True
        assert payload["validation_slices"][0]["kind"] == "out_of_sample"
