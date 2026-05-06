import json
from pathlib import Path

from quant.optimization.artifact_generation import load_source_report
from quant.optimization.source_report_generation import (
    MonteCarloValidationConfig,
    generate_monte_carlo_source_report,
    run_monte_carlo_validation,
)
from scripts import generate_strategy_validation_source_reports as generate_reports


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def build_klines(count=180):
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


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def make_config(source_path, output_dir, **overrides):
    values = {
        "source_path": str(source_path),
        "strategy_id": "ma_crossover",
        "candidate_version": "review-2026-05-04-BTCUSDT-ma_crossover",
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "input_kind": "auto",
        "output_dir": str(output_dir),
        "generated_at": 1777827600,
        "min_trades": 1,
        "min_trade_count": 3,
        "run_count": 30,
        "seed": 7,
        "survival_threshold": 0.5,
    }
    values.update(overrides)
    return MonteCarloValidationConfig(**values)


def test_run_monte_carlo_validation_is_deterministic_for_same_seed():
    config = make_config(
        source_path="/tmp/local.json",
        output_dir="/tmp/source-reports",
        run_count=20,
        seed=11,
        min_trade_count=3,
    )
    trade_pnls = [1.2, -0.4, 0.8, 2.5, -0.3, 1.1]

    first = run_monte_carlo_validation(trade_pnls=trade_pnls, config=config)
    second = run_monte_carlo_validation(trade_pnls=trade_pnls, config=config)

    assert first == second
    assert first["run_count"] == 20
    assert first["seed"] == 11
    assert first["method"] == "hybrid"
    assert first["pass_count"] + first["fail_count"] == 20


def test_generate_monte_carlo_source_report_from_local_kline_json(tmp_path):
    source_path = write_json(tmp_path / "history" / "BTCUSDT-1m.json", build_klines())
    output_dir = tmp_path / "source-reports"

    result = generate_monte_carlo_source_report(
        make_config(
            source_path,
            output_dir,
            run_count=40,
            min_trade_count=5,
            survival_threshold=0.3,
            perturbation_dimensions=[
                "trade_order_shuffle",
                "return_perturbation",
                "slippage_fee_perturbation",
            ],
        )
    )

    assert result.status == "PASS"
    assert result.reason_codes == []
    assert result.source_report_path is not None
    source_report_path = Path(result.source_report_path)
    assert source_report_path.exists()
    assert source_report_path.name.endswith("-mc.json")

    payload = json.loads(source_report_path.read_text(encoding="utf-8"))
    assert payload["source_report_id"] == result.source_report_id
    assert payload["summary"]["trade_count"] >= 1
    assert payload["validation_slices"][0]["kind"] == "out_of_sample"
    assert payload["monte_carlo_validation"]["method"] == "hybrid"
    assert payload["monte_carlo_validation"]["run_count"] == 40
    assert payload["monte_carlo_validation"]["seed"] == 7
    assert payload["monte_carlo_validation"]["survival_threshold"] == 0.3
    assert payload["monte_carlo_survival_rate"] >= 0.3
    assert payload["provenance"]["input_kind"] == "klines"
    assert payload["safety_flags"] == {
        "analytics_modified_live_state": False,
        "broker_called": False,
        "contains_real_credentials": False,
        "live_orders_sent": False,
        "network_access_used": False,
        "real_credentials_read": False,
    }

    loaded = load_source_report(source_report_path)
    assert loaded.monte_carlo_validation is not None
    assert loaded.monte_carlo_validation.method == "hybrid"
    assert loaded.monte_carlo_validation.run_count == 40


def test_monte_carlo_generation_skips_when_trade_count_is_insufficient(tmp_path):
    source_path = write_json(tmp_path / "history" / "BTCUSDT-1m.json", build_klines())
    output_dir = tmp_path / "source-reports"

    result = generate_monte_carlo_source_report(
        make_config(
            source_path,
            output_dir,
            min_trade_count=999,
        )
    )

    assert result.status == "SKIPPED"
    assert result.reason_codes == ["insufficient_monte_carlo_trades"]
    assert result.source_report_path is None
    assert result.metrics["trade_count"] >= 1
    assert not list(output_dir.rglob("*.json"))


def test_monte_carlo_generation_skips_when_survival_rate_is_below_threshold(tmp_path):
    source_path = write_json(
        tmp_path / "backtests" / "oos-result.json",
        {
            "status": "completed",
            "kind": "out_of_sample",
            "validation_window": {
                "kind": "out_of_sample",
                "start_timestamp": 1701006000,
                "end_timestamp": 1701012000,
                "bar_count": 120,
            },
            "realized_trade_pnls": [8.0, -5.0, 3.0, -2.0, 4.0, -1.0, 2.0, -0.5],
            "metrics": {
                "trade_count": 8,
                "total_net_pnl": 8.5,
                "max_drawdown": 5.0,
                "win_rate": 0.5,
                "sharpe_ratio": 0.9,
            },
        },
    )
    output_dir = tmp_path / "source-reports"

    result = generate_monte_carlo_source_report(
        make_config(
            source_path,
            output_dir,
            input_kind="backtest_result",
            run_count=30,
            min_trade_count=5,
            survival_threshold=0.2,
            max_drawdown_limit=0.0,
            perturbation_dimensions=["trade_order_shuffle"],
        )
    )

    assert result.status == "SKIPPED"
    assert result.reason_codes == ["monte_carlo_survival_rate_below_threshold"]
    assert result.source_report_path is None
    assert result.metrics["monte_carlo_survival_rate"] == 0.0
    assert result.metrics["monte_carlo_validation"]["method"] == "trade_shuffle"
    assert not list(output_dir.rglob("*.json"))


def test_monte_carlo_generation_rejects_config_examples_as_real_evidence(tmp_path):
    source_path = (
        PROJECT_ROOT / "config" / "examples" / "strategy-validation-source-report.example.json"
    )
    result = generate_monte_carlo_source_report(
        make_config(source_path, tmp_path / "source-reports")
    )

    assert result.status == "FAIL"
    assert "source_report_generation_failed" in result.reason_codes
    assert result.source_report_path is None


def test_generation_script_writes_monte_carlo_aggregate_report(tmp_path):
    source_path = write_json(tmp_path / "history" / "BTCUSDT-1m.json", build_klines())
    output_dir = tmp_path / "source-reports"
    report_output_path = tmp_path / "generation-latest.json"

    report = generate_reports.run_strategy_validation_source_report_generation(
        source_paths=[source_path],
        strategy_id="ma_crossover",
        candidate_version="review-2026-05-04-BTCUSDT-ma_crossover",
        symbol="BTCUSDT",
        output_dir=output_dir,
        report_output_path=report_output_path,
        timestamp=1777827600,
        generation_kind="monte_carlo",
        monte_carlo_run_count=40,
        min_monte_carlo_trades=5,
        min_monte_carlo_survival_rate=0.3,
    )

    assert report["status"] == "PASS"
    assert report["source_report_generation_scope"] == "H-OPT-015"
    assert report["generation_kind"] == "monte_carlo"
    assert report["generated_source_report_count"] == 1
    assert report["reason_codes"] == []
    assert Path(report["source_report_paths"][0]).exists()
    assert json.loads(report_output_path.read_text(encoding="utf-8"))["status"] == "PASS"


def test_generate_monte_carlo_source_report_from_explicit_backtest_result_payload(tmp_path):
    source_path = write_json(
        tmp_path / "backtests" / "oos-result.json",
        {
            "status": "completed",
            "kind": "out_of_sample",
            "validation_window": {
                "kind": "out_of_sample",
                "start_timestamp": 1701006000,
                "end_timestamp": 1701012000,
                "bar_count": 120,
            },
            "realized_trade_pnls": [10.0, -3.0, 4.0, 2.0, -1.0, 6.0, -2.0, 3.0],
            "metrics": {
                "trade_count": 8,
                "total_net_pnl": 19.0,
                "max_drawdown": 7.0,
                "win_rate": 0.625,
                "sharpe_ratio": 1.1,
            },
        },
    )
    output_dir = tmp_path / "source-reports"

    result = generate_monte_carlo_source_report(
        make_config(
            source_path,
            output_dir,
            input_kind="backtest_result",
            run_count=20,
            min_trade_count=5,
            survival_threshold=0.1,
        )
    )

    assert result.status == "PASS"
    payload = json.loads(Path(result.source_report_path).read_text(encoding="utf-8"))
    assert payload["provenance"]["input_kind"] == "backtest_result"
    assert payload["summary"]["trade_count"] == 8
    assert payload["monte_carlo_validation"]["run_count"] == 20
    assert payload["monte_carlo_survival_rate"] >= 0.1
