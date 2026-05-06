from pathlib import Path

from scripts.run_pass_artifact_discovery import (
    build_candidates,
    build_filtered_matrix,
    discover_sources,
    walk_forward_capacity,
)


def test_walk_forward_capacity_requires_enough_windows():
    assert walk_forward_capacity(20, 12, 8, 8) == 1
    assert walk_forward_capacity(900, 600, 100, 100) == 3
    assert walk_forward_capacity(1000, 600, 100, 100) == 4


def test_discover_sources_rejects_smoke_and_keeps_long_history(tmp_path):
    smoke = tmp_path / "btcusdt-5m-20-latest.json"
    smoke.write_text("{}")
    long = tmp_path / "btcusdt-5m-10k-latest.json"
    long.write_text("{}")
    matrix = tmp_path / "matrix.json"
    matrix.write_text(
        """
        {
          "status": "PASS",
          "symbol": "BTCUSDT",
          "timeframes": {
            "5m": {
              "status": "PASS",
              "bar_count": 20,
              "output_path": "btcusdt-5m-20-latest.json",
              "quality_report": {"passed": true},
              "safety_flags": {"public_market_data_only": true, "real_credentials_read": false, "broker_called": false, "account_or_order_endpoint_called": false, "live_orders_sent": false}
            },
            "1h": {
              "status": "PASS",
              "bar_count": 10000,
              "output_path": "btcusdt-5m-10k-latest.json",
              "quality_report": {"passed": true},
              "safety_flags": {"public_market_data_only": true, "real_credentials_read": false, "broker_called": false, "account_or_order_endpoint_called": false, "live_orders_sent": false}
            }
          }
        }
        """
    )

    sources, rejections = discover_sources(
        [matrix],
        min_bars=1000,
        allow_smoke_inputs=False,
        symbols=None,
        timeframes=None,
    )

    assert [(s["symbol"], s["timeframe"], s["bar_count"]) for s in sources] == [
        ("BTCUSDT", "1h", 10000)
    ]
    assert {r["reason"] for r in rejections} >= {"source_bar_count_below_minimum"}


def test_build_candidates_prechecks_walk_forward_capacity(tmp_path):
    source = {
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "status": "PASS",
        "bar_count": 1000,
        "output_path": str(tmp_path / "btcusdt-5m-10k-latest.json"),
        "quality_report": {"passed": True},
        "safety_flags": {"public_market_data_only": True},
    }
    candidates, rejections = build_candidates(
        sources=[source],
        strategy_ids=["ma_crossover"],
        max_params_per_strategy=2,
        external_candidates_path=None,
        include_external=False,
        include_generated=True,
        min_walk_forward_windows=3,
        default_train_bars=600,
        default_test_bars=100,
        default_step_bars=100,
        default_holdout_ratio=0.2,
        default_min_trade_count=10,
        max_candidates=10,
    )

    assert len(candidates) == 2
    assert not rejections
    assert candidates[0]["symbol"] == "BTCUSDT"
    assert candidates[0]["timeframe"] == "5m"
    assert candidates[0]["window_config"]["train_bars"] == 600


def test_filtered_matrix_uses_symbols_schema():
    matrix = build_filtered_matrix(
        [
            {
                "symbol": "BTCUSDT",
                "timeframe": "5m",
                "bar_count": 10000,
                "output_path": "logs/public-market-data/btcusdt-5m-10k-latest.json",
                "quality_report": {"passed": True},
                "safety_flags": {"public_market_data_only": True},
                "reason_codes": [],
                "sha256": "abc",
            }
        ],
        generated_at=123,
    )

    assert matrix["status"] == "PASS"
    assert matrix["symbols"]["BTCUSDT"]["timeframes"]["5m"]["bar_count"] == 10000
