import json
from pathlib import Path

from scripts import run_public_btcusdt_strategy_validation_search as search


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def build_input(path):
    return write_json(
        path,
        {
            "status": "PASS",
            "exchange": "binance",
            "symbol": "BTCUSDT",
            "timeframe": "5m",
            "klines": [
                {
                    "timestamp": 1700000000 + index * 300,
                    "open": 100.0 + index,
                    "high": 101.0 + index,
                    "low": 99.0 + index,
                    "close": 100.5 + index,
                    "volume": 1000.0 + index,
                }
                for index in range(120)
            ],
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
                    "trade_count": 6,
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
                    "trade_count": 6,
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


def test_search_records_best_skipped_candidate(monkeypatch, tmp_path):
    source_path = build_input(tmp_path / "public" / "btcusdt-5m.json")
    output_path = tmp_path / "search-latest.json"
    calls = []
    survival_rates = [0.1, 0.4]

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

    report = search.run_public_btcusdt_strategy_validation_search(
        input_path=source_path,
        output_path=output_path,
        trial_output_dir=tmp_path / "trials",
        source_report_dir=tmp_path / "source-reports",
        artifact_dir=tmp_path / "artifacts",
        latest_validator_output_path=None,
        max_trials=2,
        fast_windows=[1, 2],
        slow_windows=[4],
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
    assert report["completed_trial_count"] == 2
    assert report["best_candidate"]["parameters"]["fast_window"] == 2
    assert report["best_candidate"]["metrics"]["monte_carlo_survival_rate"] == 0.4
    assert calls[0]["generation_kind"] == "aggregate"
    assert calls[0]["min_monte_carlo_trades"] == 5
    assert "-data-" in calls[0]["candidate_version"]
    assert "-fw1-sw4-" in calls[0]["candidate_version"]
    assert report["live_orders_sent"] is False
    assert report["broker_called"] is False


def test_search_passes_candidate_and_writes_latest_validator(
    monkeypatch,
    tmp_path,
):
    source_path = build_input(tmp_path / "public" / "btcusdt-5m.json")
    output_path = tmp_path / "search-latest.json"
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

    report = search.run_public_btcusdt_strategy_validation_search(
        input_path=source_path,
        output_path=output_path,
        trial_output_dir=tmp_path / "trials",
        source_report_dir=tmp_path / "source-reports",
        artifact_dir=tmp_path / "artifacts",
        latest_validator_output_path=latest_validator_output,
        max_trials=2,
        fast_windows=[1, 2],
        slow_windows=[4],
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
    assert json.loads(latest_validator_output.read_text(encoding="utf-8"))[
        "artifact_count"
    ] == 1
    assert len(calls) == 2


def test_main_returns_skipped_when_input_is_missing(tmp_path):
    output_path = tmp_path / "search-latest.json"

    exit_code = search.main(
        [
            "--input",
            str(tmp_path / "missing.json"),
            "--output",
            str(output_path),
        ]
    )

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert report["status"] == "SKIPPED"
    assert report["reason_codes"] == ["source_input_missing"]
