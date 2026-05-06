import json
from pathlib import Path

from quant.optimization.artifact_generation import load_source_report
from quant.optimization.source_report_generation import (
    HistoricalValidationWindowConfig,
    generate_oos_source_report,
    split_historical_validation_window,
)
from quant.data.schemas.market import Kline
from scripts import generate_strategy_validation_source_reports as generate_reports


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def build_klines(count=90):
    closes = []
    pattern = [100.0, 103.0, 98.0, 104.0, 97.0, 105.0]
    for index in range(count):
        closes.append(pattern[index % len(pattern)] + index * 0.01)
    klines = []
    for index, close in enumerate(closes):
        previous = closes[index - 1] if index > 0 else close
        klines.append(
            {
                "timestamp": 1700000000 + index * 60,
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
        "output_dir": str(output_dir),
        "generated_at": 1777827600,
        "min_train_bars": 40,
        "min_holdout_bars": 20,
        "min_trades": 1,
    }
    values.update(overrides)
    return HistoricalValidationWindowConfig(**values)


def test_split_historical_validation_window_uses_independent_holdout():
    klines = [
        Kline(
            timestamp=1700000000 + index * 60,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=1000.0,
        )
        for index in range(80)
    ]
    config = make_config(
        source_path="/tmp/local-klines.json",
        output_dir="/tmp/source-reports",
        holdout_bars=20,
    )

    train, holdout, window = split_historical_validation_window(klines, config)

    assert len(train) == 60
    assert len(holdout) == 20
    assert train[-1].timestamp < holdout[0].timestamp
    assert window["train"]["end_timestamp"] == train[-1].timestamp
    assert window["holdout"]["start_timestamp"] == holdout[0].timestamp


def test_generate_oos_source_report_from_local_kline_json(tmp_path):
    source_path = write_json(tmp_path / "history" / "BTCUSDT-1m.json", build_klines())
    output_dir = tmp_path / "source-reports"

    result = generate_oos_source_report(make_config(source_path, output_dir))

    assert result.status == "PASS"
    assert result.success is True
    assert result.reason_codes == []
    assert result.source_report_path is not None
    source_report_path = Path(result.source_report_path)
    assert source_report_path.exists()
    assert source_report_path.parent == output_dir / "BTCUSDT" / "ma_crossover"

    payload = json.loads(source_report_path.read_text(encoding="utf-8"))
    assert payload["source_report_id"] == result.source_report_id
    assert payload["strategy_id"] == "ma_crossover"
    assert payload["candidate_version"] == "review-2026-05-04-BTCUSDT-ma_crossover"
    assert payload["symbol"] == "BTCUSDT"
    assert payload["source_path"] == str(source_path)
    assert payload["summary"]["trade_count"] >= 1
    assert payload["validation_slices"][0]["kind"] == "out_of_sample"
    assert payload["validation_slices"][0]["trade_count"] == payload["summary"]["trade_count"]
    assert payload["provenance"]["input_kind"] == "klines"
    assert payload["provenance"]["window"]["train"]["end_timestamp"] < payload["provenance"]["window"]["holdout"]["start_timestamp"]
    assert payload["safety_flags"] == {
        "analytics_modified_live_state": False,
        "broker_called": False,
        "contains_real_credentials": False,
        "live_orders_sent": False,
        "network_access_used": False,
        "real_credentials_read": False,
    }

    loaded = load_source_report(source_report_path)
    assert loaded.validation_slices[0].kind == "out_of_sample"


def test_generate_oos_source_report_from_smartqtf_market_data_envelope(tmp_path):
    source_path = write_json(
        tmp_path / "logs" / "real-data-feature-latest.json",
        {
            "run": {
                "mode": "read_only_public_market_data_to_dry_run_execution_plan",
                "live_orders_sent": False,
                "credentials_required": False,
                "broker_called": False,
            },
            "data_layer": {
                "input_type": "MultiTimeframeKlineBatch",
                "payload": {
                    "schema_version": "1.0",
                    "symbol": "BTC-USDT",
                    "execution_timeframe": "5m",
                    "execution": {
                        "schema_version": "1.0",
                        "symbol": "BTC-USDT",
                        "timeframe": "5m",
                        "venue": "okx",
                        "role": "execution",
                        "klines": build_klines(),
                    },
                },
            },
        },
    )
    output_dir = tmp_path / "source-reports"

    result = generate_oos_source_report(
        make_config(source_path, output_dir, timeframe="5m")
    )

    assert result.status == "PASS"
    payload = json.loads(Path(result.source_report_path).read_text(encoding="utf-8"))
    assert payload["provenance"]["input_kind"] == "klines"
    assert payload["provenance"]["source_path"] == str(source_path)
    assert payload["validation_slices"][0]["kind"] == "out_of_sample"


def test_generation_skips_quality_failed_local_klines(tmp_path):
    klines = build_klines()
    klines[4]["timestamp"] = klines[3]["timestamp"]
    source_path = write_json(tmp_path / "history" / "bad-klines.json", klines)
    output_dir = tmp_path / "source-reports"

    result = generate_oos_source_report(make_config(source_path, output_dir))

    assert result.status == "SKIPPED"
    assert result.reason_codes == ["data_quality_failed"]
    assert result.source_report_path is None
    assert not list(output_dir.rglob("*.json"))


def test_generation_rejects_config_examples_as_oos_evidence(tmp_path):
    source_path = PROJECT_ROOT / "config" / "examples" / "strategy-validation-source-report.example.json"
    result = generate_oos_source_report(make_config(source_path, tmp_path / "source-reports"))

    assert result.status == "FAIL"
    assert "source_report_generation_failed" in result.reason_codes
    assert result.source_report_path is None


def test_generate_oos_source_report_from_explicit_oos_backtest_result_payload(tmp_path):
    source_path = write_json(
        tmp_path / "backtests" / "oos-result.json",
        {
            "status": "completed",
            "kind": "out_of_sample",
            "validation_window": {
                "kind": "out_of_sample",
                "start_timestamp": 1700006000,
                "end_timestamp": 1700012000,
                "bar_count": 100,
            },
            "metrics": {
                "trade_count": 7,
                "total_net_pnl": 12.5,
                "max_drawdown": 0.08,
                "win_rate": 0.57,
                "sharpe_ratio": 1.2,
            },
        },
    )
    output_dir = tmp_path / "source-reports"

    result = generate_oos_source_report(
        make_config(source_path, output_dir, input_kind="backtest_result")
    )

    assert result.status == "PASS"
    payload = json.loads(Path(result.source_report_path).read_text(encoding="utf-8"))
    assert payload["summary"]["trade_count"] == 7
    assert payload["summary"]["total_net_pnl"] == 12.5
    assert payload["summary"]["max_drawdown"] == 0.08
    assert payload["summary"]["win_rate"] == 0.57
    assert payload["summary"]["sharpe_ratio"] == 1.2
    assert payload["validation_slices"][0]["kind"] == "out_of_sample"
    assert payload["provenance"]["input_kind"] == "backtest_result"


def test_backtest_result_must_be_explicitly_oos(tmp_path):
    source_path = write_json(
        tmp_path / "backtests" / "insample-result.json",
        {
            "status": "completed",
            "metrics": {
                "trade_count": 7,
                "total_net_pnl": 12.5,
                "max_drawdown": 0.08,
                "win_rate": 0.57,
            },
            "validation_window": {
                "start_timestamp": 1700006000,
                "end_timestamp": 1700012000,
            },
        },
    )

    result = generate_oos_source_report(
        make_config(source_path, tmp_path / "source-reports", input_kind="backtest_result")
    )

    assert result.status == "SKIPPED"
    assert result.reason_codes == ["backtest_result_not_marked_out_of_sample"]
    assert result.source_report_path is None


def test_generation_script_writes_aggregate_report(tmp_path):
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
    )

    assert report["status"] == "PASS"
    assert report["generated_source_report_count"] == 1
    assert report["source_input_count"] == 1
    assert report["reason_codes"] == []
    assert report["safety_flags"]["live_orders_sent"] is False
    assert Path(report["source_report_paths"][0]).exists()
    assert json.loads(report_output_path.read_text(encoding="utf-8"))["status"] == "PASS"
