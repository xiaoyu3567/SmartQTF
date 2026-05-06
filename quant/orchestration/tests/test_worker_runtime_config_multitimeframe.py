import json
from pathlib import Path

from quant.config import load_runtime_config
from quant.orchestration.worker_runtime import SmartQTFWorkerRuntime


def test_multitimeframe_paper_example_config_exposes_runtime_kline_and_testflow(tmp_path):
    config_path = _write_temp_runtime_config(tmp_path)

    runtime = SmartQTFWorkerRuntime(config_path=config_path)
    run_once_payload = runtime.run_once(requested_at=1700000600, index=4, batch_id="config-mtf")
    kline_payload = runtime.kline(symbol="BTCUSDT", timeframe="5m")
    testflow_payload = runtime.testflow()

    assert run_once_payload["batch_id"] == "config-mtf"
    assert kline_payload["available"] is True
    assert kline_payload["reason"] is None
    assert kline_payload["execution_timeframe"] == "5m"
    assert kline_payload["context_timeframes"] == ["15m", "1h", "4h"]
    assert kline_payload["requested_channel"]["role"] == "execution"
    assert kline_payload["requested_channel"]["coverage"]["status"] == "complete"
    assert kline_payload["worker_cache"]["source"] == "latest_pipeline_report"
    assert sorted(kline_payload["worker_cache"]["batches"]) == ["15m", "1h", "4h", "5m"]
    assert kline_payload["provider_rest_fallback"]["available"] is False
    assert kline_payload["provider_rest_fallback"]["reason"] == "disabled_by_default_fixture_mode"

    assert testflow_payload["available"] is True
    assert testflow_payload["reason"] is None
    assert testflow_payload["latest_report"]["context"]["timeframe"] == "5m"
    assert testflow_payload["multi_timeframe"]["enabled"] is True
    assert testflow_payload["multi_timeframe"]["execution_timeframe"] == "5m"
    assert testflow_payload["multi_timeframe"]["context_timeframes"] == ["15m", "1h", "4h"]
    assert testflow_payload["status"]["safety"]["external_exchange_access"] is False
    assert testflow_payload["status"]["safety"]["live_order_submission"] is False


def _write_temp_runtime_config(tmp_path):
    project_root = Path(__file__).resolve().parents[3]
    example_path = project_root / "config" / "examples" / "paper-runtime-multitimeframe.example.json"
    payload = json.loads(example_path.read_text(encoding="utf-8"))
    payload["logging"]["decision_log_path"] = str(tmp_path / "decisions.jsonl")
    payload["logging"]["order_log_path"] = str(tmp_path / "orders.jsonl")
    payload["logging"]["fill_log_path"] = str(tmp_path / "fills.jsonl")
    payload["logging"]["pipeline_report_dir"] = str(tmp_path / "pipeline-runs")
    payload["broker"]["order_log_path"] = str(tmp_path / "broker-orders.jsonl")

    config_path = tmp_path / "paper-runtime-multitimeframe.json"
    config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    loaded = load_runtime_config(config_path)
    assert loaded.multi_timeframe.enabled is True
    assert loaded.multi_timeframe.execution_timeframe == "5m"
    assert loaded.multi_timeframe.context_timeframes == ["15m", "1h", "4h"]
    return config_path
