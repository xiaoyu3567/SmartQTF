#!/usr/bin/env python
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.decision import AIDecisionAdvisor, ChatCompletionsJSONClient, FixtureAIClient
from quant.schemas import AIDecisionAdvisorRequest, AssetClass, MarketType, PayloadSource, TraceContext


DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "logs" / "ai-decision-advisor-validation" / "latest.json"
LIVE_VALIDATION_ENV = "SMARTQTF_RUN_AI_DECISION_ADVISOR_TEST"


def run_ai_decision_advisor_validation(
    *,
    symbol: str,
    timeframe: str,
    model_name: str,
    endpoint: str | None = None,
    response_fixture: str | Path | None = None,
    timestamp: int | None = None,
    run_id: str = "ai-advisor-validation",
    request_id: str = "ai-advisor-validation-request",
    output_path: str | Path | None = None,
    source: PayloadSource = PayloadSource.PAPER,
    asset_class: AssetClass = AssetClass.CRYPTO,
    market_type: MarketType = MarketType.SPOT,
    market_context: dict[str, Any] | None = None,
    feature_context: dict[str, Any] | None = None,
    regime_context: dict[str, Any] | None = None,
    strategy_context: dict[str, Any] | None = None,
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    timestamp = int(time.time()) if timestamp is None else timestamp
    if response_fixture is None and os.getenv(LIVE_VALIDATION_ENV) != "1":
        report = {
            "success": False,
            "status": "SKIPPED",
            "message": f"set {LIVE_VALIDATION_ENV}=1 or pass --response-fixture",
            "response_source": "ai_provider",
            "read_only": True,
            "live_orders_sent": False,
            "risk_bypassed": False,
            "contains_real_credentials": False,
        }
        return _write_report(report, output_path)

    resolved_endpoint = (endpoint or os.getenv("SMARTQTF_AI_ADVISOR_ENDPOINT", "")).strip()
    if response_fixture is None:
        blocked_report = _real_provider_preflight_report(resolved_endpoint)
        if blocked_report is not None:
            return _write_report(blocked_report, output_path)

    request_payload = AIDecisionAdvisorRequest(
        request_id=request_id,
        timestamp=timestamp,
        symbol=symbol,
        asset_class=asset_class,
        market_type=market_type,
        timeframe=timeframe,
        model_name=model_name,
        trace=TraceContext(
            run_id=run_id,
            source=source,
            symbol=symbol,
            timeframe=timeframe,
            timestamp=timestamp,
        ),
        market_context=market_context or {},
        feature_context=feature_context or {},
        regime_context=regime_context or {},
        strategy_context=strategy_context or {},
        constraints=constraints or {"advice_only": True, "must_pass_risk_later": True},
    )

    if response_fixture is not None:
        client = FixtureAIClient.from_path(response_fixture)
        response_source = "fixture"
    else:
        client = ChatCompletionsJSONClient(
            endpoint=resolved_endpoint,
            api_key=os.getenv("SMARTQTF_AI_ADVISOR_API_KEY"),
        )
        response_source = "ai_provider"

    advisor = AIDecisionAdvisor(client)
    try:
        suggestion = advisor.request_suggestion(request_payload)
    except Exception as exc:
        report = {
            "success": False,
            "status": "FAIL",
            "message": str(exc),
            "response_source": response_source,
            "read_only": True,
            "live_orders_sent": False,
            "risk_bypassed": False,
            "contains_real_credentials": False,
        }
        return _write_report(report, output_path)

    report = {
        "success": True,
        "status": "PASS",
        "message": "AI decision advisor response passed sandbox validation",
        "response_source": response_source,
        "read_only": True,
        "live_orders_sent": False,
        "risk_bypassed": False,
        "contains_real_credentials": False,
        "proxy": {
            "SMARTQTF_USE_PROXY": os.getenv("SMARTQTF_USE_PROXY"),
            "required_for_real_provider": response_fixture is None,
        },
        "request": request_payload.to_payload(),
        "suggestion": suggestion.to_payload(),
    }
    return _write_report(report, output_path)


def _real_provider_preflight_report(endpoint: str) -> dict[str, Any] | None:
    checks = []
    if os.getenv("SMARTQTF_USE_PROXY") != "1":
        checks.append(
            {
                "status": "FAIL",
                "category": "proxy",
                "message": "set SMARTQTF_USE_PROXY=1 before real AI provider validation",
            }
        )
    if not endpoint:
        checks.append(
            {
                "status": "FAIL",
                "category": "configuration",
                "message": "set SMARTQTF_AI_ADVISOR_ENDPOINT or pass --endpoint",
            }
        )
    if not os.getenv("SMARTQTF_AI_ADVISOR_API_KEY"):
        checks.append(
            {
                "status": "FAIL",
                "category": "credential",
                "message": "set SMARTQTF_AI_ADVISOR_API_KEY for real AI provider validation",
            }
        )
    if not checks:
        return None

    return {
        "success": False,
        "status": "FAIL",
        "message": "real AI decision advisor validation is blocked by missing environment requirements",
        "response_source": "ai_provider",
        "checks": checks,
        "read_only": True,
        "live_orders_sent": False,
        "risk_bypassed": False,
        "contains_real_credentials": False,
        "proxy": {
            "SMARTQTF_USE_PROXY": os.getenv("SMARTQTF_USE_PROXY"),
            "required_for_real_provider": True,
        },
    }


def _write_report(report: dict[str, Any], output_path: str | Path | None) -> dict[str, Any]:
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def _json_arg(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise argparse.ArgumentTypeError("JSON argument must decode to an object")
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the SmartQTF AI decision advisor boundary without bypassing Risk or Execution."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--model", default=os.getenv("SMARTQTF_AI_ADVISOR_MODEL", "smartqtf-fixture-model"))
    parser.add_argument("--endpoint", default=os.getenv("SMARTQTF_AI_ADVISOR_ENDPOINT"))
    parser.add_argument("--response-fixture")
    parser.add_argument("--timestamp", type=int)
    parser.add_argument("--run-id", default="ai-advisor-validation")
    parser.add_argument("--request-id", default="ai-advisor-validation-request")
    parser.add_argument("--source", choices=[item.value for item in PayloadSource], default=PayloadSource.PAPER.value)
    parser.add_argument("--asset-class", choices=[item.value for item in AssetClass], default=AssetClass.CRYPTO.value)
    parser.add_argument("--market-type", choices=[item.value for item in MarketType], default=MarketType.SPOT.value)
    parser.add_argument("--market-context-json", type=_json_arg, default={})
    parser.add_argument("--feature-context-json", type=_json_arg, default={})
    parser.add_argument("--regime-context-json", type=_json_arg, default={})
    parser.add_argument("--strategy-context-json", type=_json_arg, default={})
    parser.add_argument("--constraints-json", type=_json_arg, default={})
    parser.add_argument("--output", default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args(argv)

    report = run_ai_decision_advisor_validation(
        symbol=args.symbol,
        timeframe=args.timeframe,
        model_name=args.model,
        endpoint=args.endpoint,
        response_fixture=args.response_fixture,
        timestamp=args.timestamp,
        run_id=args.run_id,
        request_id=args.request_id,
        output_path=args.output,
        source=PayloadSource(args.source),
        asset_class=AssetClass(args.asset_class),
        market_type=MarketType(args.market_type),
        market_context=args.market_context_json,
        feature_context=args.feature_context_json,
        regime_context=args.regime_context_json,
        strategy_context=args.strategy_context_json,
        constraints=args.constraints_json,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["status"] == "PASS":
        return 0
    if report["status"] == "SKIPPED":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
