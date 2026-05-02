import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib import request

from quant.decision.ai_sandbox import AIDecisionSuggestionSandbox, DISALLOWED_AI_DIRECTIVE_KEYS
from quant.proxy import build_proxy_opener, proxy_enabled
from quant.schemas import AIDecisionAdvisorRequest, AIDecisionSuggestion


AI_DECISION_OUTPUT_SCHEMA: dict[str, Any] = {
    "name": "AIDecisionSuggestion",
    "description": (
        "Replayable SmartQTF AI decision advice. It is not an order, risk approval, "
        "portfolio allocation, or execution command. The candidate must be a complete "
        "DecisionIntent object using the exact field names below."
    ),
    "required_top_level_keys": [
        "suggestion_id",
        "timestamp",
        "candidate",
    ],
    "required_candidate_keys": [
        "decision_id",
        "timestamp",
        "symbol",
        "asset_class",
        "market_type",
        "strategy_id",
        "strategy_version",
        "action",
        "order_type",
        "quantity",
        "confidence",
        "reason_codes",
        "trace",
    ],
    "candidate_contract": {
        "decision_id": "string, unique replayable decision id",
        "timestamp": "integer unix timestamp copied from the request timestamp",
        "symbol": "string copied exactly from request.symbol, e.g. BTCUSDT",
        "asset_class": "one of: crypto, us_equity, china_a",
        "market_type": "one of: spot, margin, perpetual, futures",
        "strategy_id": "string identifying the advisory source, e.g. ai_decision_advisor",
        "strategy_version": "string advisory strategy version, e.g. v1",
        "regime": "optional string or null",
        "action": "one of: open_long, close_long, open_short, close_short, hold",
        "order_type": "one of: market, limit, stop. Use market for hold/no-trade advice.",
        "quantity": "positive number. For hold/no-trade advice use a tiny advisory placeholder such as 0.000001.",
        "limit_price": "number or null. Required only when order_type is limit.",
        "stop_loss": "number or null",
        "take_profit": "number or null",
        "stop_loss_targets": "array, default []",
        "take_profit_targets": "array, default []",
        "time_in_force": "one of: gtc, ioc, fok. Default gtc.",
        "reduce_only": "boolean, default false",
        "confidence": "number between 0 and 1",
        "reason_codes": "non-empty array of stable machine-readable strings, not prose",
        "trace": {
            "run_id": "copied exactly from request.trace.run_id",
            "source": "copied exactly from request.trace.source",
            "symbol": "copied exactly from request.symbol",
            "timeframe": "copied exactly from request.timeframe",
            "timestamp": "copied exactly from request.trace.timestamp",
            "bar_index": "copied from request.trace.bar_index, or null",
        },
    },
    "forbidden_candidate_keys": [
        "type",
        "decision",
        "stance",
        "rationale",
        "conditions_to_reconsider",
        "conditions_for_reconsideration",
        "must_pass_later",
    ],
    "forbidden_keys": sorted(DISALLOWED_AI_DIRECTIVE_KEYS),
    "example": {
        "suggestion_id": "ai-suggestion-BTCUSDT-1710000000",
        "timestamp": 1710000000,
        "candidate": {
            "decision_id": "ai-decision-BTCUSDT-1710000000",
            "timestamp": 1710000000,
            "symbol": "BTCUSDT",
            "asset_class": "crypto",
            "market_type": "spot",
            "strategy_id": "ai_decision_advisor",
            "strategy_version": "v1",
            "regime": "unknown",
            "action": "hold",
            "order_type": "market",
            "quantity": 0.000001,
            "limit_price": None,
            "stop_loss": None,
            "take_profit": None,
            "stop_loss_targets": [],
            "take_profit_targets": [],
            "time_in_force": "gtc",
            "reduce_only": False,
            "confidence": 0.25,
            "reason_codes": ["AI_INSUFFICIENT_CONTEXT", "AI_HOLD_ADVICE"],
            "trace": {
                "run_id": "paper-ai-001",
                "source": "paper",
                "symbol": "BTCUSDT",
                "timeframe": "1m",
                "timestamp": 1710000000,
                "bar_index": None,
            },
        },
    },
}


class AIDecisionModelClient(Protocol):
    def create_json(
        self,
        *,
        model_name: str,
        messages: list[dict[str, str]],
        response_schema: Mapping[str, Any],
    ) -> Mapping[str, Any] | str:
        raise NotImplementedError


class ChatCompletionsJSONClient:
    """Minimal OpenAI-compatible chat completions client using stdlib HTTP."""

    def __init__(
        self,
        endpoint: str,
        api_key: str | None = None,
        *,
        timeout: float = 30.0,
        use_proxy: bool | None = None,
    ):
        if not endpoint:
            raise ValueError("AI advisor endpoint must not be empty")
        self.endpoint = normalize_openai_chat_completions_endpoint(endpoint)
        self.api_key = api_key
        self.timeout = timeout
        self.use_proxy = proxy_enabled() if use_proxy is None else use_proxy

    @classmethod
    def from_env(cls) -> "ChatCompletionsJSONClient":
        endpoint = os.getenv("SMARTQTF_AI_ADVISOR_ENDPOINT", "").strip()
        api_key = os.getenv("SMARTQTF_AI_ADVISOR_API_KEY")
        timeout = float(os.getenv("SMARTQTF_AI_ADVISOR_TIMEOUT", "30"))
        return cls(endpoint=endpoint, api_key=api_key, timeout=timeout)

    def create_json(
        self,
        *,
        model_name: str,
        messages: list[dict[str, str]],
        response_schema: Mapping[str, Any],
    ) -> Mapping[str, Any] | str:
        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "metadata": {"smartqtf_response_schema": dict(response_schema)},
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "SmartQTF/1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        http_request = request.Request(self.endpoint, data=body, headers=headers, method="POST")
        opener = build_proxy_opener() if self.use_proxy else None
        open_fn = opener.open if opener is not None else request.urlopen
        with open_fn(http_request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))


class FixtureAIClient:
    """Deterministic client for local rehearsal of the AI advisor boundary."""

    def __init__(self, payload: Mapping[str, Any] | str):
        self.payload = payload

    @classmethod
    def from_path(cls, path: str | Path) -> "FixtureAIClient":
        text = Path(path).read_text(encoding="utf-8")
        try:
            return cls(json.loads(text))
        except json.JSONDecodeError:
            return cls(text)

    def create_json(
        self,
        *,
        model_name: str,
        messages: list[dict[str, str]],
        response_schema: Mapping[str, Any],
    ) -> Mapping[str, Any] | str:
        return self.payload


class AIDecisionAdvisor:
    """Call a model client and sandbox the response into replayable advice."""

    def __init__(
        self,
        client: AIDecisionModelClient,
        *,
        sandbox: AIDecisionSuggestionSandbox | None = None,
    ):
        self.client = client
        self.sandbox = sandbox or AIDecisionSuggestionSandbox()

    def request_suggestion(self, advisor_request: AIDecisionAdvisorRequest) -> AIDecisionSuggestion:
        self._reject_request_directives(advisor_request)
        messages = self.build_messages(advisor_request)
        raw_response = self.client.create_json(
            model_name=advisor_request.model_name,
            messages=messages,
            response_schema=AI_DECISION_OUTPUT_SCHEMA,
        )
        raw_hash = stable_payload_hash(raw_response)
        payload = self._extract_suggestion_payload(raw_response)
        payload = self._with_replay_defaults(payload, advisor_request, messages, raw_hash)
        suggestion = self.sandbox.evaluate(payload)
        self._assert_suggestion_matches_request(suggestion, advisor_request)
        return suggestion

    def build_messages(self, advisor_request: AIDecisionAdvisorRequest) -> list[dict[str, str]]:
        request_payload = advisor_request.to_payload()
        return [
            {
                "role": "system",
                "content": (
                    "You are a read-only SmartQTF decision advisor. Return JSON only. "
                    "The JSON must be an AIDecisionSuggestion containing a complete DecisionIntent candidate. "
                    "Use exact SmartQTF field names: decision_id, timestamp, symbol, asset_class, "
                    "market_type, strategy_id, strategy_version, action, order_type, quantity, "
                    "confidence, reason_codes, and trace. Do not use natural-language wrapper fields "
                    "such as type, decision, stance, rationale, conditions_to_reconsider, or must_pass_later. "
                    "For no-trade advice use action='hold', order_type='market', and a tiny positive "
                    "advisory placeholder quantity such as 0.000001. Do not include risk approval, "
                    "portfolio allocation, broker, order intent, client order id, or execution command fields. "
                    "The candidate still must pass Risk, Portfolio, and Execution later."
                ),
            },
            {
                "role": "user",
                "content": canonical_json(
                    {
                        "request": request_payload,
                        "output_contract": AI_DECISION_OUTPUT_SCHEMA,
                    }
                ),
            },
        ]

    def _reject_request_directives(self, advisor_request: AIDecisionAdvisorRequest) -> None:
        payload = advisor_request.to_payload()
        for key in (
            "market_context",
            "feature_context",
            "regime_context",
            "strategy_context",
            "portfolio_context",
            "constraints",
            "metadata",
        ):
            _reject_disallowed_directives(payload.get(key, {}), f"request.{key}")

    def _extract_suggestion_payload(self, raw_response: Mapping[str, Any] | str) -> dict[str, Any]:
        if isinstance(raw_response, str):
            return _loads_json_object(raw_response)

        if not isinstance(raw_response, Mapping):
            raise ValueError("AI advisor response must be a mapping or JSON string")

        if "suggestion_id" in raw_response or "candidate" in raw_response:
            return dict(raw_response)

        content = _extract_provider_text(raw_response)
        if isinstance(content, Mapping):
            return dict(content)
        if isinstance(content, str):
            return _loads_json_object(content)

        raise ValueError("AI advisor response did not contain a JSON suggestion payload")

    def _with_replay_defaults(
        self,
        payload: Mapping[str, Any],
        advisor_request: AIDecisionAdvisorRequest,
        messages: list[dict[str, str]],
        raw_response_hash: str,
    ) -> dict[str, Any]:
        normalized = dict(payload)
        normalized.setdefault("advisor_name", advisor_request.advisor_name)
        normalized.setdefault("model_name", advisor_request.model_name)
        normalized.setdefault("prompt_id", advisor_request.prompt_id)
        normalized.setdefault("prompt_hash", stable_payload_hash(messages))
        normalized.setdefault("raw_response_hash", raw_response_hash)
        metadata = dict(normalized.get("metadata") or {})
        metadata.setdefault("request_id", advisor_request.request_id)
        metadata.setdefault("response_source", "ai_model")
        normalized["metadata"] = metadata
        return normalized

    def _assert_suggestion_matches_request(
        self,
        suggestion: AIDecisionSuggestion,
        advisor_request: AIDecisionAdvisorRequest,
    ) -> None:
        candidate = suggestion.candidate
        if candidate.symbol != advisor_request.symbol:
            raise ValueError("AI suggestion candidate symbol does not match advisor request")
        if _enum_value(candidate.asset_class) != _enum_value(advisor_request.asset_class):
            raise ValueError("AI suggestion candidate asset_class does not match advisor request")
        if _enum_value(candidate.market_type) != _enum_value(advisor_request.market_type):
            raise ValueError("AI suggestion candidate market_type does not match advisor request")

        trace = candidate.trace
        request_trace = advisor_request.trace
        if trace.run_id != request_trace.run_id:
            raise ValueError("AI suggestion trace run_id does not match advisor request")
        if _enum_value(trace.source) != _enum_value(request_trace.source):
            raise ValueError("AI suggestion trace source does not match advisor request")
        if trace.symbol != advisor_request.symbol:
            raise ValueError("AI suggestion trace symbol does not match advisor request")
        if advisor_request.timeframe is not None and trace.timeframe != advisor_request.timeframe:
            raise ValueError("AI suggestion trace timeframe does not match advisor request")


def normalize_openai_chat_completions_endpoint(endpoint: str) -> str:
    """Accept either a provider root URL or the full OpenAI-compatible chat completions URL."""

    cleaned = endpoint.strip().rstrip("/")
    if not cleaned:
        raise ValueError("AI advisor endpoint must not be empty")
    if cleaned.endswith("/chat/completions"):
        return cleaned
    if cleaned.endswith("/v1"):
        return f"{cleaned}/chat/completions"
    return f"{cleaned}/v1/chat/completions"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def stable_payload_hash(value: Any) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = canonical_json(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _reject_disallowed_directives(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if key_text in DISALLOWED_AI_DIRECTIVE_KEYS:
                raise ValueError(f"AI advisor request cannot include execution directive: {path}.{key_text}")
            _reject_disallowed_directives(item, f"{path}.{key_text}")
        return

    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_disallowed_directives(item, f"{path}[{index}]")


def _extract_provider_text(payload: Mapping[str, Any]) -> Mapping[str, Any] | str | None:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
        if isinstance(message, Mapping):
            content = message.get("content")
            if isinstance(content, (str, Mapping)):
                return content

    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, Mapping):
                continue
            content_items = item.get("content")
            if not isinstance(content_items, list):
                continue
            for content_item in content_items:
                if not isinstance(content_item, Mapping):
                    continue
                text = content_item.get("text")
                if isinstance(text, str):
                    return text
    return None


def _loads_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("AI advisor JSON response must be an object")
    return payload


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)
