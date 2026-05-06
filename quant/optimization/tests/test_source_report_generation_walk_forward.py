import json
from pathlib import Path

from quant.optimization.artifact_generation import load_source_report
from quant.optimization.source_report_generation import (
    WalkForwardWindowConfig,
    generate_walk_forward_slices,
    generate_walk_forward_source_report,
)
from quant.data.schemas.market import Kline
from scripts import generate_strategy_validation_source_reports as generate_reports


def build_klines(count=120):
    pattern = [100.0, 103.0, 98.0, 104.0, 97.0, 105.0]
    closes = [pattern[index % len(pattern)] + index * 0.01 for index in range(count)]
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
        "input_kind": "klines",
        "output_dir": str(output_dir),
        "generated_at": 1777827600,
        "min_trades": 1,
        "train_bars": 30,
        "test_bars": 20,
        "step_bars": 20,
        "min_windows": 3,
        "min_pass_rate": 0.67,
    }
    values.update(overrides)
    return WalkForwardWindowConfig(**values)


def test_generate_walk_forward_slices_builds_independent_windows():
    klines = [
        Kline(
            timestamp=1700000000 + index * 60,
            open=100.0,
            high=106.0,
            low=96.0,
            close=100.0 + ((-1) ** index) * 2.0,
            volume=1000.0,
        )
        for index in range(100)
    ]
    config = make_config(
        source_path="/tmp/local-klines.json",
        output_dir="/tmp/source-reports",
    )

    slices, metadata = generate_walk_forward_slices(klines, config)

    assert len(slices) == 3
    assert metadata["walk_forward_window_count"] == 3
    assert metadata["walk_forward_pass_count"] == 3
    assert metadata["walk_forward_pass_rate"] == 1.0
    assert [item.kind for item in slices] == ["walk_forward"] * 3
    assert [item.name for item in slices] == [
        "wf-001-1700001800-1700002940",
        "wf-002-1700003000-1700004140",
        "wf-003-1700004200-1700005340",
    ]
    for window in metadata["walk_forward_windows"]:
        assert window["train"]["end_timestamp"] < window["test"]["start_timestamp"]
        assert window["test"]["bar_count"] == 20


def test_generate_walk_forward_source_report_from_local_kline_json(tmp_path):
    source_path = write_json(tmp_path / "history" / "BTCUSDT-1m.json", build_klines())
    output_dir = tmp_path / "source-reports"

    result = generate_walk_forward_source_report(make_config(source_path, output_dir))

    assert result.status == "PASS"
    assert result.success is True
    assert result.reason_codes == []
    assert result.source_report_path is not None
    source_report_path = Path(result.source_report_path)
    assert source_report_path.exists()
    assert source_report_path.name.endswith("-wf.json")

    payload = json.loads(source_report_path.read_text(encoding="utf-8"))
    assert payload["source_report_id"] == result.source_report_id
    assert payload["summary"]["trade_count"] >= 3
    assert payload["walk_forward_window_count"] == 4
    assert payload["walk_forward_pass_count"] == 4
    assert payload["walk_forward_pass_rate"] == 1.0
    assert len(payload["validation_slices"]) == 4
    assert {item["kind"] for item in payload["validation_slices"]} == {"walk_forward"}
    assert payload["validation_slices"][0]["name"].startswith("wf-001-")
    assert payload["provenance"]["input_kind"] == "klines"
    assert payload["provenance"]["window"]["min_windows"] == 3
    assert payload["safety_flags"] == {
        "analytics_modified_live_state": False,
        "broker_called": False,
        "contains_real_credentials": False,
        "live_orders_sent": False,
        "network_access_used": False,
        "real_credentials_read": False,
    }

    loaded = load_source_report(source_report_path)
    assert len(loaded.validation_slices) == 4
    assert loaded.validation_slices[0].kind == "walk_forward"


def test_walk_forward_generation_skips_when_windows_are_insufficient(tmp_path):
    source_path = write_json(tmp_path / "history" / "too-short.json", build_klines(70))
    output_dir = tmp_path / "source-reports"

    result = generate_walk_forward_source_report(make_config(source_path, output_dir))

    assert result.status == "SKIPPED"
    assert result.reason_codes == ["insufficient_walk_forward_windows"]
    assert result.source_report_path is None
    assert not list(output_dir.rglob("*.json"))


def test_walk_forward_generation_skips_quality_failed_klines(tmp_path):
    klines = build_klines()
    klines[4]["timestamp"] = klines[3]["timestamp"]
    source_path = write_json(tmp_path / "history" / "bad-klines.json", klines)
    output_dir = tmp_path / "source-reports"

    result = generate_walk_forward_source_report(make_config(source_path, output_dir))

    assert result.status == "SKIPPED"
    assert result.reason_codes == ["walk_forward_input_quality_failed"]
    assert result.source_report_path is None
    assert not list(output_dir.rglob("*.json"))


def test_walk_forward_generation_skips_when_pass_rate_is_below_threshold(tmp_path):
    source_path = write_json(tmp_path / "history" / "BTCUSDT-1m.json", build_klines())
    output_dir = tmp_path / "source-reports"

    result = generate_walk_forward_source_report(
        make_config(source_path, output_dir, min_trades=100, min_pass_rate=1.0)
    )

    assert result.status == "SKIPPED"
    assert result.reason_codes == ["walk_forward_pass_rate_below_threshold"]
    assert result.metrics["walk_forward"]["walk_forward_window_count"] == 4
    assert result.metrics["walk_forward"]["walk_forward_pass_count"] == 0
    assert result.source_report_path is None


def test_generation_script_writes_walk_forward_aggregate_report(tmp_path):
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
        generation_kind="walk_forward",
        train_bars=30,
        test_bars=20,
        step_bars=20,
        min_walk_forward_windows=3,
        min_walk_forward_pass_rate=0.67,
    )

    assert report["status"] == "PASS"
    assert report["source_report_generation_scope"] == "H-OPT-014"
    assert report["generation_kind"] == "walk_forward"
    assert report["generated_source_report_count"] == 1
    assert report["reason_codes"] == []
    assert Path(report["source_report_paths"][0]).exists()
    assert json.loads(report_output_path.read_text(encoding="utf-8"))["status"] == "PASS"
