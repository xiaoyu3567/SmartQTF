import json

from adapters.exchange.binance import BinanceAdapterError
from quant.data.quality import timeframe_to_seconds
from quant.data.schemas.market import Kline, KlineBatch
from scripts.fetch_public_market_universe import run_public_market_universe_fetch


class FakeMultiSymbolPublicAdapter:
    def __init__(self, klines_by_symbol_timeframe, failures_by_symbol_timeframe=None):
        self.klines_by_symbol_timeframe = klines_by_symbol_timeframe
        self.failures_by_symbol_timeframe = failures_by_symbol_timeframe or {}
        self.requests = []

    def get_klines(self, symbol, timeframe, *, limit=100, start_ts=None, end_ts=None):
        self.requests.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "limit": limit,
                "start_ts": start_ts,
                "end_ts": end_ts,
            }
        )
        failure = self.failures_by_symbol_timeframe.get((symbol, timeframe))
        if failure is not None:
            raise failure
        klines = [
            kline
            for kline in self.klines_by_symbol_timeframe.get((symbol, timeframe), [])
            if (start_ts is None or kline.timestamp >= start_ts)
            and (end_ts is None or kline.timestamp <= end_ts)
        ]
        return KlineBatch(
            symbol=symbol,
            timeframe=timeframe,
            venue="binance",
            klines=sorted(klines, key=lambda item: item.timestamp)[:limit],
        )


class FakeDiscoveryPublicAdapter(FakeMultiSymbolPublicAdapter):
    def __init__(
        self,
        klines_by_symbol_timeframe,
        discovery_symbols,
        failures_by_symbol_timeframe=None,
        discovery_failure=None,
    ):
        super().__init__(
            klines_by_symbol_timeframe,
            failures_by_symbol_timeframe=failures_by_symbol_timeframe,
        )
        self.discovery_symbols = discovery_symbols
        self.discovery_failure = discovery_failure
        self.discovery_requests = []

    def discover_public_symbols(self, *, exchange, market_type):
        self.discovery_requests.append({"exchange": exchange, "market_type": market_type})
        if self.discovery_failure is not None:
            raise self.discovery_failure
        return list(self.discovery_symbols)


def test_public_market_universe_writes_ranked_multi_symbol_matrix(tmp_path):
    adapter = FakeMultiSymbolPublicAdapter(
        {
            ("BTCUSDT", "5m"): [
                _kline(2400, 100.0),
                _kline(2700, 101.0),
                _kline(3000, 102.0),
                _kline(3300, 103.0),
            ],
            ("BTCUSDT", "15m"): [
                _kline(0, 100.0),
                _kline(900, 101.0),
                _kline(1800, 102.0),
                _kline(2700, 103.0),
            ],
            ("ETHUSDT", "5m"): [
                _kline(2400, 200.0),
                _kline(2700, 201.0),
                _kline(3000, 202.0),
                _kline(3300, 203.0),
            ],
            ("ETHUSDT", "15m"): [
                _kline(0, 200.0),
                _kline(900, 201.0),
                _kline(1800, 202.0),
                _kline(2700, 203.0),
            ],
        }
    )

    report = run_public_market_universe_fetch(
        exchange="binance",
        symbols=["ETHUSDT", "BTCUSDT"],
        timeframes=["5m", "15m"],
        required_timeframes=["5m", "15m"],
        target_bars=4,
        page_limit=4,
        max_pages=1,
        min_pass_symbols=2,
        output_dir=tmp_path,
        summary_output=tmp_path / "public-universe-matrix-latest.json",
        timestamp=3601,
        adapter=adapter,
    )

    saved_summary = json.loads(
        (tmp_path / "public-universe-matrix-latest.json").read_text(encoding="utf-8")
    )
    assert report == saved_summary
    assert report["status"] == "PASS"
    assert report["public_universe_matrix_ready"] is True
    assert report["h_data_015_ready"] is False
    assert report["h_data_015_completion_gate"] == {
        "schema_version": "1.0",
        "ready": False,
        "min_ready_symbols": 2,
        "ready_symbol_count": 0,
        "ready_symbols": [],
        "required_timeframes": ["1m", "5m", "15m", "1h", "4h", "1d"],
        "requested_timeframes_complete": False,
        "min_target_bars": 10000,
        "target_bars_requested": 4,
        "target_bars_complete": False,
        "blocked_symbols": [
            {
                "symbol": "BTCUSDT",
                "status": "PASS",
                "ready": False,
                "missing_completion_timeframes": ["1m", "1h", "4h", "1d"],
                "non_pass_completion_timeframes": [],
                "insufficient_completion_timeframes": ["5m", "15m"],
            },
            {
                "symbol": "ETHUSDT",
                "status": "PASS",
                "ready": False,
                "missing_completion_timeframes": ["1m", "1h", "4h", "1d"],
                "non_pass_completion_timeframes": [],
                "insufficient_completion_timeframes": ["5m", "15m"],
            },
        ],
        "reason_codes": [
            "completion_timeframes_not_requested",
            "h_data_015_ready_symbol_count_below_minimum",
            "target_bars_below_h_data_015_completion_minimum",
        ],
    }
    assert report["pass_symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert [item["symbol"] for item in report["allowlist"]] == ["BTCUSDT", "ETHUSDT"]
    assert report["allowlist"][0]["quality_score"] == 1.0
    assert report["allowlist"][0]["data_quality_score"] == 1.0
    assert report["allowlist"][0]["coverage_score"] == 1.0
    assert report["allowlist"][0]["bar_coverage_score"] == 1.0
    assert report["allowlist"][0]["required_timeframe_coverage_score"] == 1.0
    assert report["allowlist"][0]["pass_timeframe_count"] == 2
    assert report["allowlist"][0]["quality_diagnostics"] == {
        "issue_count": 0,
        "fatal_issue_count": 0,
        "issue_code_counts": {},
        "quality_reason_codes": ["public_kline_quality_ready"],
        "min_required_bar_count": 4,
        "requested_bar_coverage_score": 1.0,
        "required_timeframe_coverage_score": 1.0,
        "walk_forward_min_required_bar_count": 4,
        "walk_forward_ready_proxy": True,
    }
    assert report["quality_summary"] == {
        "schema_version": "1.0",
        "evaluated_symbol_count": 2,
        "walk_forward_ready_proxy_symbol_count": 2,
        "walk_forward_ready_proxy_symbols": ["BTCUSDT", "ETHUSDT"],
        "total_bar_count": 16,
        "issue_code_counts": {},
        "reason_codes": ["public_universe_quality_ready"],
    }
    assert report["candidate_input_ranking"]["rank_criteria"] == [
        "data_quality_score_desc",
        "quality_score_desc",
        "coverage_score_desc",
        "tradeability_proxy_desc",
        "symbol_asc",
    ]
    assert report["candidate_input_ranking"]["candidate_count"] == 2
    assert report["candidate_input_ranking"]["recommended_symbol_count"] == 2
    assert report["candidate_input_ranking"]["recommended_symbols"] == [
        "BTCUSDT",
        "ETHUSDT",
    ]
    assert report["candidate_input_ranking"]["reason_codes"] == [
        "candidate_input_ranking_ready"
    ]
    assert report["candidate_input_ranking"]["top_candidate"] == {
        "rank": 1,
        "symbol": "BTCUSDT",
        "exchange": "binance",
        "market_type": "spot",
        "status": "PASS",
        "data_quality_score": 1.0,
        "quality_score": 1.0,
        "coverage_score": 1.0,
        "timeframe_coverage_score": 1.0,
        "required_timeframe_coverage_score": 1.0,
        "bar_coverage_score": 1.0,
        "tradeability_proxy": 1.0,
        "public_discovery_tradeability_proxy": None,
        "public_discovery_turnover_24h": None,
        "pass_timeframe_count": 2,
        "required_pass_timeframe_count": 2,
        "requested_timeframe_count": 2,
        "total_bar_count": 8,
        "first_timestamp": 0,
        "last_timestamp": 3300,
        "walk_forward_ready_proxy": True,
        "walk_forward_min_required_bar_count": 4,
        "quality_reason_codes": ["public_kline_quality_ready"],
        "selection_reason_codes": [
            "no_public_discovery_tradeability_proxy",
            "required_timeframes_ready",
            "symbol_history_passed",
            "target_bar_coverage_complete",
            "walk_forward_ready_proxy",
        ],
        "output_path": str(tmp_path / "binance-btcusdt-mtf-4-latest.json"),
        "sha256": report["allowlist"][0]["sha256"],
    }
    assert len(report["fingerprint"]) == 64
    assert len(report["candidate_input_ranking"]["fingerprint"]) == 64
    assert report["safety_flags"]["public_market_data_only"] is True
    assert report["safety_flags"]["real_credentials_read"] is False
    assert report["safety_flags"]["broker_called"] is False
    assert (tmp_path / "binance-btcusdt-mtf-4-latest.json").exists()
    assert (tmp_path / "binance-ethusdt-mtf-4-latest.json").exists()


def test_public_market_universe_records_partial_without_faking_minimum(tmp_path):
    adapter = FakeMultiSymbolPublicAdapter(
        {
            ("BTCUSDT", "5m"): [_kline(0, 100.0), _kline(300, 101.0)],
            ("ETHUSDT", "5m"): [],
        },
        failures_by_symbol_timeframe={
            ("ETHUSDT", "5m"): BinanceAdapterError("DNS lookup failed")
        },
    )

    report = run_public_market_universe_fetch(
        exchange="binance",
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframes=["5m"],
        required_timeframes=["5m"],
        target_bars=2,
        page_limit=2,
        max_pages=1,
        min_pass_symbols=2,
        output_dir=tmp_path,
        summary_output=tmp_path / "summary.json",
        timestamp=601,
        adapter=adapter,
    )

    assert report["status"] == "PARTIAL"
    assert report["public_universe_matrix_ready"] is False
    assert report["pass_symbols"] == ["BTCUSDT"]
    assert report["skipped_symbols"] == ["ETHUSDT"]
    assert "min_pass_symbols_not_reached" in report["reason_codes"]
    assert "public_market_data_unavailable" in report["reason_codes"]
    skipped_symbol = report["symbols"]["ETHUSDT"]
    assert skipped_symbol["status"] == "SKIPPED"
    assert skipped_symbol["safety_flags"]["live_orders_sent"] is False


def test_public_market_universe_quality_summary_records_gappy_symbol(tmp_path):
    adapter = FakeMultiSymbolPublicAdapter(
        {
            ("BTCUSDT", "5m"): [
                _kline(0, 100.0),
                _kline(600, 102.0),
            ],
            ("ETHUSDT", "5m"): [
                _kline(0, 200.0),
                _kline(300, 201.0),
            ],
        }
    )

    report = run_public_market_universe_fetch(
        exchange="binance",
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframes=["5m"],
        required_timeframes=["5m"],
        target_bars=2,
        page_limit=3,
        max_pages=1,
        min_pass_symbols=1,
        output_dir=tmp_path,
        summary_output=tmp_path / "summary.json",
        timestamp=901,
        adapter=adapter,
    )

    assert report["status"] == "PASS"
    assert report["pass_symbols"] == ["ETHUSDT"]
    assert report["failed_symbols"] == ["BTCUSDT"]
    assert report["quality_summary"]["evaluated_symbol_count"] == 2
    assert report["quality_summary"]["walk_forward_ready_proxy_symbols"] == ["ETHUSDT"]
    assert report["quality_summary"]["issue_code_counts"] == {"missing_kline": 2}
    assert report["quality_summary"]["reason_codes"] == [
        "some_symbols_not_walk_forward_ready_proxy",
        "symbol_quality_issues_present",
    ]
    assert report["candidate_input_ranking"]["candidate_count"] == 1
    assert report["candidate_input_ranking"]["recommended_symbols"] == ["ETHUSDT"]
    assert report["candidate_input_ranking"]["top_candidate"]["symbol"] == "ETHUSDT"
    assert "candidate_input_ranking_ready" in report["candidate_input_ranking"]["reason_codes"]
    failed = report["allowlist"][1]
    assert failed["symbol"] == "BTCUSDT"
    assert failed["data_quality_score"] == 0.25
    assert failed["quality_diagnostics"]["fatal_issue_count"] == 2
    assert failed["quality_diagnostics"]["issue_code_counts"] == {"missing_kline": 2}
    assert failed["quality_diagnostics"]["quality_reason_codes"] == [
        "fatal_kline_quality_issues_present",
        "not_all_requested_timeframes_passed",
        "required_timeframe_quality_incomplete",
    ]
    assert failed["quality_diagnostics"]["walk_forward_ready_proxy"] is False


def test_public_market_universe_candidate_ranking_does_not_recommend_partial_symbol(tmp_path):
    adapter = FakeMultiSymbolPublicAdapter(
        {
            ("BTCUSDT", "5m"): [
                _kline(600, 100.0),
                _kline(900, 101.0),
            ],
            ("ETHUSDT", "5m"): [
                _kline(900, 200.0),
            ],
        }
    )

    report = run_public_market_universe_fetch(
        exchange="binance",
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframes=["5m"],
        required_timeframes=["5m"],
        target_bars=2,
        page_limit=2,
        max_pages=1,
        min_pass_symbols=2,
        output_dir=tmp_path,
        summary_output=tmp_path / "summary.json",
        timestamp=1201,
        adapter=adapter,
    )

    assert report["status"] == "PARTIAL"
    ranking = report["candidate_input_ranking"]
    assert ranking["candidate_count"] == 1
    assert ranking["recommended_symbol_count"] == 1
    assert ranking["recommended_symbols"] == ["BTCUSDT"]
    assert ranking["top_candidate"]["symbol"] == "BTCUSDT"
    assert ranking["reason_codes"] == ["recommended_symbol_count_below_minimum"]
    assert "ETHUSDT" not in [item["symbol"] for item in ranking["candidates"]]


def test_public_market_universe_h_data_015_ready_requires_full_completion_gate(tmp_path):
    timeframes = ["1m", "5m", "15m", "1h", "4h", "1d"]
    timestamp = 2_000_000_000
    adapter = FakeMultiSymbolPublicAdapter(
        {
            (symbol, timeframe): _kline_series(
                generated_at=timestamp,
                timeframe=timeframe,
                count=10000,
                base=base,
            )
            for symbol, base in (("BTCUSDT", 100.0), ("ETHUSDT", 200.0))
            for timeframe in timeframes
        }
    )

    report = run_public_market_universe_fetch(
        exchange="binance",
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframes=timeframes,
        required_timeframes=timeframes,
        target_bars=10000,
        page_limit=10000,
        max_pages=1,
        min_pass_symbols=2,
        output_dir=tmp_path,
        summary_output=tmp_path / "summary.json",
        timestamp=timestamp,
        adapter=adapter,
    )

    assert report["status"] == "PASS"
    assert report["public_universe_matrix_ready"] is True
    assert report["h_data_015_ready"] is True
    gate = report["h_data_015_completion_gate"]
    assert gate["ready"] is True
    assert gate["requested_timeframes_complete"] is True
    assert gate["target_bars_complete"] is True
    assert gate["ready_symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert gate["blocked_symbols"] == []
    assert gate["reason_codes"] == ["h_data_015_completion_gate_ready"]


def test_public_market_universe_marks_runtime_limited_symbols_skipped(tmp_path):
    adapter = FakeMultiSymbolPublicAdapter({})

    report = run_public_market_universe_fetch(
        exchange="binance",
        symbols=["BTCUSDT"],
        timeframes=["5m"],
        required_timeframes=["5m"],
        target_bars=2,
        page_limit=2,
        max_pages=1,
        min_pass_symbols=1,
        output_dir=tmp_path,
        summary_output=tmp_path / "summary.json",
        timestamp=601,
        adapter=adapter,
        max_runtime_seconds=0.000001,
    )

    assert report["status"] == "SKIPPED"
    assert report["runtime_limited"] is True
    assert report["skipped_symbols"] == ["BTCUSDT"]
    assert report["symbols"]["BTCUSDT"]["reason_codes"] == ["max_runtime_seconds_reached"]
    assert adapter.requests == []


def test_public_market_universe_discovers_ranked_public_symbol_allowlist(tmp_path):
    adapter = FakeDiscoveryPublicAdapter(
        {
            ("ETHUSDT", "5m"): [
                _kline(0, 200.0),
                _kline(300, 201.0),
                _kline(600, 202.0),
            ],
            ("SOLUSDT", "5m"): [
                _kline(0, 20.0),
                _kline(300, 21.0),
                _kline(600, 22.0),
            ],
        },
        discovery_symbols=[
            {
                "symbol": "OLDUSDT",
                "status": "BREAK",
                "quote_currency": "USDT",
                "turnover_24h": 999999.0,
            },
            {
                "symbol": "ETHUSDT",
                "status": "TRADING",
                "quote_currency": "USDT",
                "turnover_24h": 300000.0,
                "volume_24h": 1000.0,
            },
            {
                "symbol": "SOLUSDT",
                "status": "TRADING",
                "quote_currency": "USDT",
                "turnover_24h": 200000.0,
                "volume_24h": 900.0,
            },
            {
                "symbol": "DOGEBTC",
                "status": "TRADING",
                "quote_currency": "BTC",
                "turnover_24h": 400000.0,
            },
        ],
    )

    report = run_public_market_universe_fetch(
        exchange="binance",
        symbols=None,
        discover_symbols=True,
        max_discovered_symbols=2,
        discovery_quote_currencies=["USDT"],
        min_discovery_turnover_24h=100000.0,
        timeframes=["5m"],
        required_timeframes=["5m"],
        target_bars=3,
        page_limit=3,
        max_pages=1,
        min_pass_symbols=2,
        output_dir=tmp_path,
        summary_output=tmp_path / "summary.json",
        timestamp=901,
        adapter=adapter,
    )

    assert report["status"] == "PASS"
    assert report["allowlist_source"] == "public_symbol_discovery"
    assert report["symbols_requested"] == ["ETHUSDT", "SOLUSDT"]
    assert report["discovery"]["status"] == "PASS"
    assert report["discovery"]["selected_symbols"] == ["ETHUSDT", "SOLUSDT"]
    assert report["discovery"]["candidate_count"] == 2
    assert report["discovery"]["rejected"][:2] == [
        {"count": 1, "reason_code": "quote_currency_not_allowed"},
        {"count": 1, "reason_code": "symbol_status_not_tradeable"},
    ]
    assert {
        item["reason_code"] for item in report["discovery"]["rejected"][2]["items"]
    } == {"symbol_status_not_tradeable", "quote_currency_not_allowed"}
    assert report["allowlist"][0]["symbol"] == "ETHUSDT"
    assert report["allowlist"][0]["discovery_metadata"]["turnover_24h"] == 300000.0
    assert report["symbols"]["SOLUSDT"]["discovery_metadata"]["quote_currency"] == "USDT"
    assert adapter.discovery_requests == [{"exchange": "binance", "market_type": "spot"}]
    assert [request["symbol"] for request in adapter.requests] == ["ETHUSDT", "SOLUSDT"]
    assert report["safety_flags"]["public_market_data_only"] is True
    assert report["safety_flags"]["real_credentials_read"] is False
    assert report["safety_flags"]["broker_called"] is False


def test_public_market_universe_discovers_okx_public_symbols_with_ticker_tradeability(tmp_path):
    adapter = FakeDiscoveryPublicAdapter(
        {
            ("BTC-USDT", "5m"): [
                _kline(600, 100.0),
                _kline(900, 101.0),
            ],
            ("ETH-USDT", "5m"): [
                _kline(600, 200.0),
                _kline(900, 201.0),
            ],
        },
        discovery_symbols=[
            {
                "instId": "OLD-USDT",
                "instType": "SPOT",
                "state": "suspend",
                "baseCcy": "OLD",
                "quoteCcy": "USDT",
                "volCcy24h": "999999",
            },
            {
                "instId": "ETH-USDT",
                "instType": "SPOT",
                "state": "live",
                "baseCcy": "ETH",
                "quoteCcy": "USDT",
                "vol24h": "8000",
                "volCcy24h": "250000",
                "last": "2000",
            },
            {
                "instId": "BTC-USDT",
                "instType": "SPOT",
                "state": "live",
                "baseCcy": "BTC",
                "quoteCcy": "USDT",
                "vol24h": "10000",
                "volCcy24h": "300000",
                "last": "30000",
            },
            {
                "instId": "ETH-BTC",
                "instType": "SPOT",
                "state": "live",
                "baseCcy": "ETH",
                "quoteCcy": "BTC",
                "volCcy24h": "400000",
            },
            {
                "instId": "TINY-USDT",
                "instType": "SPOT",
                "state": "live",
                "baseCcy": "TINY",
                "quoteCcy": "USDT",
                "volCcy24h": "999",
            },
        ],
    )

    report = run_public_market_universe_fetch(
        exchange="okx",
        symbols=None,
        discover_symbols=True,
        max_discovered_symbols=2,
        discovery_quote_currencies=["USDT"],
        min_discovery_turnover_24h=10000.0,
        timeframes=["5m"],
        required_timeframes=["5m"],
        target_bars=2,
        page_limit=2,
        max_pages=1,
        min_pass_symbols=2,
        output_dir=tmp_path,
        summary_output=tmp_path / "summary.json",
        timestamp=1301,
        adapter=adapter,
    )

    assert report["status"] == "PASS"
    assert report["exchange"] == "okx"
    assert report["allowlist_source"] == "public_symbol_discovery"
    assert report["symbols_requested"] == ["BTC-USDT", "ETH-USDT"]
    assert report["discovery"]["source_url_or_endpoint"] == (
        "https://www.okx.com/api/v5/public/instruments"
    )
    assert report["discovery"]["source_endpoints"] == [
        "https://www.okx.com/api/v5/public/instruments",
        "https://www.okx.com/api/v5/market/tickers",
    ]
    assert report["discovery"]["selected_symbols"] == ["BTC-USDT", "ETH-USDT"]
    assert report["discovery"]["candidate_count"] == 2
    assert report["discovery"]["selected"][0]["turnover_24h"] == 300000.0
    assert report["discovery"]["selected"][0]["tradeability_proxy"] == 300000.0
    assert report["allowlist"][0]["discovery_metadata"]["instrument_type"] == "SPOT"
    assert report["candidate_input_ranking"]["recommended_symbols"] == [
        "BTC-USDT",
        "ETH-USDT",
    ]
    assert report["candidate_input_ranking"]["top_candidate"][
        "public_discovery_tradeability_proxy"
    ] == 300000.0
    assert report["discovery"]["rejected"][:3] == [
        {"count": 1, "reason_code": "quote_currency_not_allowed"},
        {"count": 1, "reason_code": "symbol_status_not_tradeable"},
        {"count": 1, "reason_code": "turnover_24h_below_minimum"},
    ]
    assert adapter.discovery_requests == [{"exchange": "okx", "market_type": "spot"}]
    assert [request["symbol"] for request in adapter.requests] == ["BTC-USDT", "ETH-USDT"]
    assert report["safety_flags"]["public_market_data_only"] is True
    assert report["safety_flags"]["real_credentials_read"] is False
    assert report["safety_flags"]["account_or_order_endpoint_called"] is False
    assert report["safety_flags"]["broker_called"] is False
    assert report["safety_flags"]["live_orders_sent"] is False


def test_public_market_universe_skips_when_symbol_discovery_unavailable(tmp_path):
    adapter = FakeDiscoveryPublicAdapter(
        {},
        discovery_symbols=[],
        discovery_failure=BinanceAdapterError("DNS lookup failed"),
    )

    report = run_public_market_universe_fetch(
        exchange="binance",
        symbols=None,
        discover_symbols=True,
        max_discovered_symbols=2,
        timeframes=["5m"],
        required_timeframes=["5m"],
        target_bars=3,
        page_limit=3,
        max_pages=1,
        min_pass_symbols=1,
        output_dir=tmp_path,
        summary_output=tmp_path / "summary.json",
        timestamp=901,
        adapter=adapter,
    )

    assert report["status"] == "SKIPPED"
    assert report["allowlist_source"] == "public_symbol_discovery"
    assert report["symbols_requested"] == []
    assert report["discovery"]["status"] == "SKIPPED"
    assert report["discovery"]["error"]["category"] == "BinanceAdapterError"
    assert "public_symbol_discovery_unavailable" in report["reason_codes"]
    assert "no_public_symbols_discovered" in report["reason_codes"]
    assert adapter.requests == []
    assert report["safety_flags"]["network_access_used"] is True
    assert report["safety_flags"]["live_orders_sent"] is False


def test_public_market_universe_reuses_existing_matching_symbol_summary(tmp_path):
    adapter = FakeMultiSymbolPublicAdapter({})
    existing_summary = run_public_market_universe_fetch(
        exchange="binance",
        symbols=["BTCUSDT"],
        timeframes=["5m"],
        required_timeframes=["5m"],
        target_bars=3,
        page_limit=3,
        max_pages=1,
        min_pass_symbols=1,
        output_dir=tmp_path,
        summary_output=None,
        timestamp=1000,
        adapter=FakeMultiSymbolPublicAdapter(
            {
                ("BTCUSDT", "5m"): [
                    _kline(0, 100.0),
                    _kline(300, 101.0),
                    _kline(600, 102.0),
                ],
            }
        ),
    )["symbols"]["BTCUSDT"]
    symbol_summary_path = tmp_path / "binance-btcusdt-mtf-3-latest.json"
    assert symbol_summary_path.exists()
    assert existing_summary["status"] == "PASS"

    report = run_public_market_universe_fetch(
        exchange="binance",
        symbols=["BTCUSDT"],
        timeframes=["5m"],
        required_timeframes=["5m"],
        target_bars=3,
        page_limit=3,
        max_pages=1,
        min_pass_symbols=1,
        output_dir=tmp_path,
        summary_output=tmp_path / "summary.json",
        timestamp=901,
        adapter=adapter,
        resume_existing_symbol_summaries=True,
    )

    assert report["status"] == "PASS"
    assert adapter.requests == []
    assert report["resume"]["enabled"] is True
    assert report["resume"]["reused_symbol_count"] == 1
    assert report["resume"]["missed_symbol_count"] == 0
    assert report["resume"]["reused_symbols"] == [
        {
            "symbol": "BTCUSDT",
            "path": str(symbol_summary_path),
            "reason_code": "resume_summary_reused",
        }
    ]
    assert "resumed_existing_symbol_summary" in report["symbols"]["BTCUSDT"]["reason_codes"]
    assert report["allowlist"][0]["output_path"] == str(symbol_summary_path)
    assert report["safety_flags"]["real_credentials_read"] is False
    assert report["safety_flags"]["broker_called"] is False
    assert report["safety_flags"]["live_orders_sent"] is False


def test_public_market_universe_refetches_when_resume_summary_contract_mismatches(tmp_path):
    symbol_summary_path = tmp_path / "binance-btcusdt-mtf-3-latest.json"
    symbol_summary_path.write_text(
        json.dumps(
            {
                "status": "PASS",
                "exchange": "binance",
                "symbol": "BTCUSDT",
                "timeframes_requested": ["15m"],
                "required_timeframes": ["15m"],
                "target_bars": 3,
                "page_limit": 3,
                "max_pages": 1,
                "safety_flags": {
                    "public_market_data_only": True,
                    "real_credentials_read": False,
                    "broker_called": False,
                    "live_orders_sent": False,
                },
            }
        ),
        encoding="utf-8",
    )
    adapter = FakeMultiSymbolPublicAdapter(
        {
            ("BTCUSDT", "5m"): [
                _kline(0, 100.0),
                _kline(300, 101.0),
                _kline(600, 102.0),
            ],
        }
    )

    report = run_public_market_universe_fetch(
        exchange="binance",
        symbols=["BTCUSDT"],
        timeframes=["5m"],
        required_timeframes=["5m"],
        target_bars=3,
        page_limit=3,
        max_pages=1,
        min_pass_symbols=1,
        output_dir=tmp_path,
        summary_output=tmp_path / "summary.json",
        timestamp=901,
        adapter=adapter,
        resume_existing_symbol_summaries=True,
    )

    assert report["status"] == "PASS"
    assert [request["symbol"] for request in adapter.requests] == ["BTCUSDT"]
    assert report["resume"]["reused_symbol_count"] == 0
    assert report["resume"]["missed_symbols"] == [
        {
            "symbol": "BTCUSDT",
            "path": str(symbol_summary_path),
            "reason_code": "resume_summary_contract_mismatch",
        }
    ]
    assert "resumed_existing_symbol_summary" not in report["symbols"]["BTCUSDT"]["reason_codes"]


def _kline(timestamp: int, close: float) -> Kline:
    return Kline(
        timestamp=timestamp,
        open=close,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=1.0,
        is_complete=True,
    )


def _kline_series(
    *,
    generated_at: int,
    timeframe: str,
    count: int,
    base: float,
) -> list[Kline]:
    interval_seconds = timeframe_to_seconds(timeframe)
    latest_timestamp = (generated_at // interval_seconds) * interval_seconds - interval_seconds
    first_timestamp = latest_timestamp - (interval_seconds * (count - 1))
    return [
        _kline(first_timestamp + (interval_seconds * index), base + float(index))
        for index in range(count)
    ]
