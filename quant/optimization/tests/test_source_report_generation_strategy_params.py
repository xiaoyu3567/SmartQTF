import json
from pathlib import Path

import pytest

from scripts import generate_strategy_validation_source_reports as generate_reports


def _build_klines(count=200):
    pattern = [100.0, 103.0, 98.0, 104.0, 97.0, 105.0]
    closes = [pattern[index % len(pattern)] + index * 0.01 for index in range(count)]
    klines = []
    for index, close in enumerate(closes):
        previous = closes[index - 1] if index > 0 else close
        klines.append(
            {
                "timestamp": 1701000000 + index * 60,
                "open": previous,
                "high": max(previous, close) + 1.0,
                "low": min(previous, close) - 1.0,
                "close": close,
                "volume": 1000.0 + index,
            }
        )
    return klines


def _write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def test_aggregate_report_carries_strategy_params_and_provenance(tmp_path):
    source_path = _write_json(tmp_path / "history" / "BTCUSDT-1m.json", {"klines": _build_klines()})
    output_dir = tmp_path / "source-reports"
    artifact_dir = tmp_path / "artifacts"

    strategy_params = {
        "fast_window": 3,
        "slow_window": 8,
        "atr_window": 5,
        "volatility_window": 8,
        "max_atr_pct": 0.2,
        "max_volatility_pct": 0.2,
        "min_trend_strength": 0.0,
    }
    report = generate_reports.run_strategy_validation_source_report_generation(
        source_paths=[source_path],
        strategy_id="ema_trend_filter",
        strategy_parameters=strategy_params,
        candidate_version="review-2026-05-04-BTCUSDT-ema-trend",
        symbol="BTCUSDT",
        output_dir=output_dir,
        report_output_path=tmp_path / "source-report-generation-latest.json",
        timestamp=1777827600,
        generation_kind="aggregate",
        train_bars=30,
        test_bars=20,
        step_bars=20,
        min_walk_forward_windows=3,
        min_walk_forward_pass_rate=0.67,
        monte_carlo_run_count=40,
        min_monte_carlo_trades=5,
        min_monte_carlo_survival_rate=0.3,
        artifact_dir=artifact_dir,
        artifact_generation_output_path=tmp_path / "artifact-generation-latest.json",
        validator_output_path=tmp_path / "validator-latest.json",
    )

    assert report["strategy_id"] == "ema_trend_filter"
    assert report["strategy_parameters"] == strategy_params
    if report["aggregate_source_report_path"] is not None:
        aggregate_payload = json.loads(
            Path(report["aggregate_source_report_path"]).read_text(encoding="utf-8")
        )
        assert aggregate_payload["provenance"]["strategy_id"] == "ema_trend_filter"
        assert aggregate_payload["provenance"]["strategy_parameters"] == strategy_params


def test_parse_strategy_params_json_requires_json_object():
    with pytest.raises(SystemExit) as exc_info:
        generate_reports.main(
            [
                "--candidate-version",
                "review-2026-05-04-BTCUSDT-test",
                "--strategy-params-json",
                "[1,2,3]",
            ]
        )
    assert exc_info.value.code == 2


def test_unsupported_strategy_parameter_returns_fail_report(tmp_path):
    source_path = _write_json(tmp_path / "history" / "BTCUSDT-1m.json", {"klines": _build_klines()})
    output_path = tmp_path / "source-report-generation-latest.json"

    report = generate_reports.run_strategy_validation_source_report_generation(
        source_paths=[source_path],
        strategy_id="donchian_breakout",
        strategy_parameters={"channel_window": 5, "bad": 1},
        candidate_version="review-2026-05-04-BTCUSDT-donchian",
        symbol="BTCUSDT",
        report_output_path=output_path,
        generation_kind="oos",
    )

    assert report["status"] == "FAIL"
    assert report["reason_codes"] == ["source_report_generation_failed"]
    assert "invalid_candidate_strategy_parameters" in report["results"][0]["message"]
