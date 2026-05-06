import json
from pathlib import Path

from scripts import run_expanded_public_btcusdt_validation_search as search


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def build_timeframe_file(path, *, timeframe):
    return write_json(
        path,
        {
            "status": "PASS",
            "exchange": "binance",
            "symbol": "BTCUSDT",
            "timeframe": timeframe,
            "klines": [
                {
                    "timestamp": 1700000000 + index * 60,
                    "open": 100.0 + index,
                    "high": 101.0 + index,
                    "low": 99.0 + index,
                    "close": 100.5 + index,
                    "volume": 1000.0 + index,
                }
                for index in range(160)
            ],
        },
    )


def build_matrix(path):
    one_minute_path = build_timeframe_file(
        path.parent / "btcusdt-1m.json",
        timeframe="1m",
    )
    five_minute_path = build_timeframe_file(
        path.parent / "btcusdt-5m.json",
        timeframe="5m",
    )
    return write_json(
        path,
        {
            "status": "PASS",
            "exchange": "binance",
            "symbol": "BTCUSDT",
            "pass_timeframes": ["1m", "5m"],
            "skipped_timeframes": ["1d"],
            "reason_codes": ["insufficient_public_klines"],
            "safety_flags": {
                "network_access_used": True,
                "public_market_data_only": True,
                "real_credentials_read": False,
                "broker_called": False,
                "live_orders_sent": False,
                "analytics_modified_live_state": False,
                "contains_real_credentials": False,
            },
            "timeframes": {
                "1m": {
                    "status": "PASS",
                    "bar_count": 160,
                    "first_timestamp": 1700000000,
                    "last_timestamp": 1700009540,
                    "output_path": str(one_minute_path),
                    "sha256": "declared-1m",
                    "reason_codes": [],
                    "quality_report": {"passed": True},
                },
                "5m": {
                    "status": "PASS",
                    "bar_count": 160,
                    "first_timestamp": 1700000000,
                    "last_timestamp": 1700047700,
                    "output_path": str(five_minute_path),
                    "sha256": "declared-5m",
                    "reason_codes": [],
                    "quality_report": {"passed": True},
                },
                "1d": {
                    "status": "SKIPPED",
                    "bar_count": 10,
                    "output_path": str(path.parent / "missing-1d.json"),
                    "reason_codes": ["insufficient_public_klines"],
                    "quality_report": {"passed": True},
                },
            },
        },
    )


def fake_aggregate_report(
    *,
    status="SKIPPED",
    reason_codes=None,
    survival_rate=0.0,
    walk_forward_pass_rate=0.0,
    artifact_paths=None,
):
    artifact_paths = artifact_paths or []
    reason_codes = reason_codes or ["monte_carlo_survival_rate_below_threshold"]
    return {
        "status": status,
        "success": status == "PASS",
        "message": "fake aggregate report",
        "reason_codes": reason_codes,
        "safety_flags": {
            "analytics_modified_live_state": False,
            "broker_called": False,
            "contains_real_credentials": False,
            "live_orders_sent": False,
            "network_access_used": False,
            "real_credentials_read": False,
        },
        "results": [
            {
                "status": "PASS",
                "message": "oos generated",
                "reason_codes": [],
                "metrics": {
                    "trade_count": 7,
                    "total_net_pnl": 12.5,
                    "max_drawdown": 1.0,
                    "win_rate": 0.5,
                },
            },
            {
                "status": "SKIPPED" if walk_forward_pass_rate < 0.67 else "PASS",
                "message": "walk-forward generated",
                "reason_codes": (
                    ["walk_forward_pass_rate_below_threshold"]
                    if walk_forward_pass_rate < 0.67
                    else []
                ),
                "metrics": {
                    "walk_forward": {
                        "walk_forward_window_count": 3,
                        "walk_forward_pass_count": int(walk_forward_pass_rate * 3),
                        "walk_forward_pass_rate": walk_forward_pass_rate,
                    }
                },
            },
            {
                "status": "SKIPPED" if survival_rate < 0.8 else "PASS",
                "message": "monte carlo generated",
                "reason_codes": (
                    ["monte_carlo_survival_rate_below_threshold"]
                    if survival_rate < 0.8
                    else []
                ),
                "metrics": {
                    "trade_count": 7,
                    "total_net_pnl": 12.5,
                    "monte_carlo_survival_rate": survival_rate,
                    "monte_carlo_run_pass_count": int(survival_rate * 10),
                    "monte_carlo_run_fail_count": 10 - int(survival_rate * 10),
                },
            },
        ],
        "aggregate_source_report_path": (
            "/tmp/source-report.json" if status == "PASS" else None
        ),
        "source_report_paths": ["/tmp/source-report.json"] if status == "PASS" else [],
        "artifact_paths": artifact_paths,
        "generated_artifact_count": len(artifact_paths),
        "validator_status": "PASS" if status == "PASS" else None,
        "h_opt_005_ready": status == "PASS",
        "h_opt_005_blockers": [] if status == "PASS" else reason_codes,
    }


def test_expanded_search_enumerates_timeframes_strategies_and_records_top_candidates(
    monkeypatch,
    tmp_path,
):
    matrix_path = build_matrix(tmp_path / "public" / "matrix.json")
    output_path = tmp_path / "expanded-search-latest.json"
    calls = []
    survival_rates = [0.1, 0.5, 0.3, 0.4]

    def fake_generation(**kwargs):
        calls.append(kwargs)
        return fake_aggregate_report(
            survival_rate=survival_rates[len(calls) - 1],
            walk_forward_pass_rate=0.5,
        )

    monkeypatch.setattr(
        search,
        "run_strategy_validation_source_report_generation",
        fake_generation,
    )

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=output_path,
        trial_output_dir=tmp_path / "trials",
        source_report_dir=tmp_path / "source-reports",
        artifact_dir=tmp_path / "artifacts",
        latest_validator_output_path=None,
        timeframes=["1m", "5m", "1d"],
        strategy_ids=["ma_crossover", "donchian_breakout"],
        max_trials=4,
        top_k=2,
        train_bars_values=[30],
        test_bars_values=[10],
        step_bars_values=[10],
        holdout_ratios=[0.3],
        min_trade_counts=[5],
        monte_carlo_run_count=10,
        timestamp=1777827600,
    )

    persisted = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["status"] == "SKIPPED"
    assert persisted["status"] == "SKIPPED"
    assert report["task_scope"] == "H-OPT-018"
    assert report["completed_trial_count"] == 4
    assert report["planned_trial_count"] == 4
    assert report["total_candidate_count"] > 4
    assert set(report["data_fingerprints"]) == {"1m", "5m"}
    assert report["skipped_timeframes"][0]["timeframe"] == "1d"
    assert len(report["top_candidates"]) == 2
    assert report["best_candidate"]["metrics"]["monte_carlo_survival_rate"] == 0.5
    assert report["all_trials"][0]["strategy_metadata"]["strategy_id"] == "ma_crossover"
    assert report["all_trials"][0]["strategy_metadata"]["cross_symbol"] is True
    assert (
        report["all_trials"][0]["strategy_metadata"]["stateless_delayed_signal"]
        is True
    )
    assert calls[0]["generation_kind"] == "aggregate"
    assert calls[0]["timeframe"] == "1m"
    assert calls[1]["timeframe"] == "5m"
    assert calls[2]["strategy_id"] == "donchian_breakout"
    assert calls[3]["strategy_id"] == "donchian_breakout"
    assert calls[0]["strategy_parameters"] == {"fast_window": 1, "slow_window": 8}
    assert "-ma_crossover-" in calls[0]["candidate_version"]
    assert report["live_orders_sent"] is False
    assert report["broker_called"] is False
    assert report["public_market_data_only"] is True


def test_expanded_search_passes_candidate_and_writes_latest_validator(
    monkeypatch,
    tmp_path,
):
    matrix_path = build_matrix(tmp_path / "public" / "matrix.json")
    output_path = tmp_path / "expanded-search-latest.json"
    latest_validator_output = tmp_path / "latest-validator.json"
    calls = []

    def fake_generation(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return fake_aggregate_report(
                survival_rate=0.2,
                walk_forward_pass_rate=0.5,
            )
        return fake_aggregate_report(
            status="PASS",
            reason_codes=[],
            survival_rate=1.0,
            walk_forward_pass_rate=1.0,
            artifact_paths=[str(tmp_path / "artifacts" / "candidate.json")],
        )

    def fake_validator(**kwargs):
        payload = {
            "status": "PASS",
            "artifact_count": len(kwargs["artifact_paths"]),
            "failed_count": 0,
        }
        Path(kwargs["output_path"]).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return payload

    monkeypatch.setattr(
        search,
        "run_strategy_validation_source_report_generation",
        fake_generation,
    )
    monkeypatch.setattr(
        search,
        "run_strategy_validation_artifacts_validation",
        fake_validator,
    )

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=output_path,
        trial_output_dir=tmp_path / "trials",
        source_report_dir=tmp_path / "source-reports",
        artifact_dir=tmp_path / "artifacts",
        latest_validator_output_path=latest_validator_output,
        timeframes=["1m", "5m"],
        strategy_ids=["ma_crossover"],
        max_trials=4,
        top_k=5,
        stop_on_first_pass=True,
        train_bars_values=[30],
        test_bars_values=[10],
        step_bars_values=[10],
        holdout_ratios=[0.3],
        min_trade_counts=[5],
        monte_carlo_run_count=10,
        timestamp=1777827600,
    )

    assert report["status"] == "PASS"
    assert report["success"] is True
    assert report["pass_count"] == 1
    assert report["artifact_count"] == 1
    assert report["validator_status"] == "PASS"
    assert report["latest_validation_report_path"] == str(latest_validator_output)
    assert report["stopped_reason"] == "first_passing_candidate_found"
    assert report["h_opt_005_ready"] is True
    assert report["h_opt_010_ready"] is True
    assert json.loads(latest_validator_output.read_text(encoding="utf-8"))[
        "artifact_count"
    ] == 1
    assert len(calls) == 2


def test_expanded_search_resumes_completed_trials(monkeypatch, tmp_path):
    matrix_path = build_matrix(tmp_path / "public" / "matrix.json")
    output_path = tmp_path / "expanded-search-latest.json"
    resume_path = tmp_path / "resume.json"
    trial = {
        "candidate_version": search._candidate_version(
            symbol="BTCUSDT",
            timeframe="1m",
            strategy_id="ma_crossover",
            data_fingerprint=search._file_fingerprint(
                tmp_path / "public" / "btcusdt-1m.json"
            ),
            strategy_parameters={"fast_window": 1, "slow_window": 8},
            window={
                "train_bars": 30,
                "test_bars": 10,
                "step_bars": 10,
                "holdout_ratio": 0.3,
                "min_trade_count": 5,
            },
        ),
        "status": "SKIPPED",
        "reason_codes": ["resumed_low_survival"],
        "metrics": {
            "monte_carlo_survival_rate": 0.2,
            "walk_forward_pass_rate": 0.5,
        },
        "h_opt_005_blockers": ["resumed_low_survival"],
        "artifact_paths": [],
        "generated_artifact_count": 0,
    }
    write_json(resume_path, {"all_trials": [trial]})

    def fail_generation(**kwargs):
        raise AssertionError("resumed trial should not rerun")

    monkeypatch.setattr(
        search,
        "run_strategy_validation_source_report_generation",
        fail_generation,
    )

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=output_path,
        latest_validator_output_path=None,
        resume_from=resume_path,
        timeframes=["1m"],
        strategy_ids=["ma_crossover"],
        max_trials=1,
        top_k=1,
        train_bars_values=[30],
        test_bars_values=[10],
        step_bars_values=[10],
        holdout_ratios=[0.3],
        min_trade_counts=[5],
        timestamp=1777827600,
    )

    assert report["completed_trial_count"] == 1
    assert report["executed_trial_count"] == 0
    assert report["resumed_trial_count"] == 1
    assert report["best_candidate"]["reason_codes"] == ["resumed_low_survival"]


def test_main_returns_skipped_when_matrix_is_missing(tmp_path):
    output_path = tmp_path / "expanded-search-latest.json"

    exit_code = search.main(
        [
            "--matrix",
            str(tmp_path / "missing.json"),
            "--output",
            str(output_path),
        ]
    )

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert report["status"] == "SKIPPED"
    assert report["reason_codes"] == ["matrix_input_missing"]


def test_default_strategy_grid_includes_macd_momentum():
    assert "macd_momentum" in search.DEFAULT_STRATEGY_IDS
    grid = search._strategy_parameter_grid("macd_momentum")

    assert len(grid) == 216
    assert grid[0] == {
        "fast_window": 8,
        "slow_window": 21,
        "signal_window": 5,
        "atr_window": 14,
        "min_histogram_pct": 0.0,
        "max_atr_pct": 0.04,
        "exit_on_signal_cross": True,
    }


def test_default_strategy_grid_includes_roc_momentum():
    assert "roc_momentum" in search.DEFAULT_STRATEGY_IDS
    grid = search._strategy_parameter_grid("roc_momentum")

    assert len(grid) == 216
    assert grid[0] == {
        "roc_window": 6,
        "trend_window": 21,
        "atr_window": 14,
        "min_roc_pct": 0.005,
        "max_atr_pct": 0.04,
        "exit_roc_pct": -0.005,
        "exit_on_trend_loss": True,
    }


def test_default_strategy_grid_includes_stochastic_reversion():
    assert "stochastic_reversion" in search.DEFAULT_STRATEGY_IDS
    grid = search._strategy_parameter_grid("stochastic_reversion")

    assert len(grid) == 144
    assert grid[0] == {
        "k_window": 9,
        "d_window": 3,
        "oversold": 15.0,
        "overbought": 75.0,
        "exit_k": 45.0,
        "exit_on_midline": True,
    }


def test_default_strategy_grid_includes_ema_pullback_reentry():
    assert "ema_pullback_reentry" in search.DEFAULT_STRATEGY_IDS
    grid = search._strategy_parameter_grid("ema_pullback_reentry")

    assert len(grid) == 288
    assert grid[0] == {
        "fast_window": 8,
        "slow_window": 34,
        "rsi_window": 7,
        "pullback_rsi": 35.0,
        "reentry_rsi": 50.0,
        "exit_rsi": 65.0,
        "atr_window": 14,
        "max_atr_pct": 0.04,
        "exit_on_trend_loss": True,
    }


def test_default_strategy_grid_includes_atr_channel_reversion():
    assert "atr_channel_reversion" in search.DEFAULT_STRATEGY_IDS
    grid = search._strategy_parameter_grid("atr_channel_reversion")

    assert len(grid) == 216
    assert grid[0] == {
        "ema_window": 13,
        "atr_window": 10,
        "atr_multiplier": 1.0,
        "min_atr_pct": 0.0,
        "max_atr_pct": 0.04,
        "exit_midline": True,
    }


def test_default_strategy_grid_includes_gap_reversal():
    assert "gap_reversal" in search.DEFAULT_STRATEGY_IDS
    grid = search._strategy_parameter_grid("gap_reversal")

    assert len(grid) == 432
    assert grid[0] == {
        "atr_window": 10,
        "volume_window": 10,
        "min_gap_pct": 0.0015,
        "min_reclaim_ratio": 0.25,
        "min_volume_ratio": 0.8,
        "max_atr_pct": 0.04,
        "exit_on_up_gap": True,
    }


def test_default_strategy_grid_includes_gap_continuation_breakout():
    assert "gap_continuation_breakout" in search.DEFAULT_STRATEGY_IDS
    grid = search._strategy_parameter_grid("gap_continuation_breakout")

    assert len(grid) == 1296
    assert grid[0] == {
        "atr_window": 10,
        "volume_window": 10,
        "min_gap_pct": 0.0015,
        "min_follow_through_ratio": 0.25,
        "min_volume_ratio": 0.8,
        "min_atr_pct": 0.0,
        "max_atr_pct": 0.04,
        "exit_on_down_gap": True,
    }


def test_default_strategy_grid_includes_liquidity_sweep_reversal():
    assert "liquidity_sweep_reversal" in search.DEFAULT_STRATEGY_IDS
    grid = search._strategy_parameter_grid("liquidity_sweep_reversal")

    assert len(grid) == 1728
    assert grid[0] == {
        "range_window": 10,
        "atr_window": 10,
        "volume_window": 10,
        "min_sweep_pct": 0.001,
        "min_close_position": 0.55,
        "min_volume_ratio": 0.8,
        "min_atr_pct": 0.0,
        "max_atr_pct": 0.04,
        "exit_on_bearish_sweep": True,
    }


def test_default_strategy_grid_includes_volatility_squeeze_breakout():
    assert "volatility_squeeze_breakout" in search.DEFAULT_STRATEGY_IDS
    grid = search._strategy_parameter_grid("volatility_squeeze_breakout")

    assert len(grid) == 1152
    assert grid[0] == {
        "breakout_window": 10,
        "bb_window": 14,
        "squeeze_window": 10,
        "bandwidth_stddev": 1.5,
        "max_squeeze_ratio": 0.75,
        "min_volume_ratio": 0.8,
        "atr_window": 14,
        "min_atr_pct": 0.0,
        "max_atr_pct": 0.04,
        "exit_on_midline_loss": True,
    }


def test_default_strategy_grid_includes_range_compression_breakout():
    assert "range_compression_breakout" in search.DEFAULT_STRATEGY_IDS
    grid = search._strategy_parameter_grid("range_compression_breakout")

    assert len(grid) == 2592
    assert grid[0] == {
        "breakout_window": 10,
        "compression_window": 10,
        "volume_window": 10,
        "atr_window": 14,
        "max_range_width_pct": 0.012,
        "max_compression_ratio": 0.65,
        "min_volume_ratio": 0.8,
        "min_atr_pct": 0.0,
        "max_atr_pct": 0.04,
        "exit_on_midline_loss": True,
    }


def test_default_strategy_grid_includes_trend_pullback_breakout():
    assert "trend_pullback_breakout" in search.DEFAULT_STRATEGY_IDS
    grid = search._strategy_parameter_grid("trend_pullback_breakout")

    assert len(grid) == 10368
    assert grid[0] == {
        "fast_window": 8,
        "slow_window": 34,
        "breakout_window": 10,
        "pullback_window": 5,
        "volume_window": 10,
        "atr_window": 14,
        "min_pullback_depth_pct": 0.001,
        "max_pullback_depth_pct": 0.02,
        "min_trend_spread_pct": 0.0,
        "min_volume_ratio": 0.8,
        "min_atr_pct": 0.0,
        "max_atr_pct": 0.04,
        "exit_on_fast_ema_loss": True,
    }


def test_default_strategy_grid_includes_chandelier_breakout():
    assert "chandelier_breakout" in search.DEFAULT_STRATEGY_IDS
    grid = search._strategy_parameter_grid("chandelier_breakout")

    assert len(grid) == 3888
    assert grid[0] == {
        "entry_window": 10,
        "exit_window": 10,
        "atr_window": 10,
        "atr_multiplier": 1.5,
        "volume_window": 10,
        "min_volume_ratio": 0.8,
        "min_atr_pct": 0.0,
        "max_atr_pct": 0.04,
        "exit_on_chandelier_loss": True,
    }


def test_default_strategy_grid_includes_rolling_vwap_reversion():
    assert "rolling_vwap_reversion" in search.DEFAULT_STRATEGY_IDS
    grid = search._strategy_parameter_grid("rolling_vwap_reversion")

    assert len(grid) == 1296
    assert grid[0] == {
        "vwap_window": 10,
        "volume_window": 10,
        "atr_window": 10,
        "entry_band_pct": 0.006,
        "min_volume_ratio": 0.8,
        "min_atr_pct": 0.0,
        "max_atr_pct": 0.04,
        "exit_on_vwap_reclaim": True,
    }


def test_default_strategy_grid_metadata_identifies_regime_aware_candidates(tmp_path):
    matrix_path = build_matrix(tmp_path / "public" / "matrix.json")

    report = search.run_expanded_public_btcusdt_validation_search(
        matrix_path=matrix_path,
        output_path=None,
        latest_validator_output_path=None,
        timeframes=["1m"],
        strategy_ids=["ema_trend_filter", "rolling_vwap_reversion"],
        max_trials=4,
        phases=["coarse"],
        train_bars_values=[30],
        test_bars_values=[10],
        step_bars_values=[10],
        holdout_ratios=[0.2],
        min_trade_counts=[5],
        timestamp=1777827600,
    )

    metadata_by_strategy = {
        trial["strategy_id"]: trial["strategy_metadata"]
        for trial in report["all_trials"]
    }
    assert set(metadata_by_strategy) == {
        "ema_trend_filter",
        "rolling_vwap_reversion",
    }
    assert metadata_by_strategy["ema_trend_filter"]["regime_aware"] is True
    assert (
        metadata_by_strategy["rolling_vwap_reversion"][
            "regime_context_contract_version"
        ]
        == "1.0"
    )
    assert "candidate_market_regime" in metadata_by_strategy[
        "rolling_vwap_reversion"
    ]["feature_requirements"]
