from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from quant.optimization.candidate_strategies import (
    SUPPORTED_CANDIDATE_STRATEGY_IDS,
    candidate_strategy_metadata,
    normalize_candidate_strategy_parameters,
)


EXTERNAL_CANDIDATE_SCHEMA_VERSION = "1.0"
EXTERNAL_CANDIDATE_REQUIRED_FIELDS = (
    "symbol",
    "timeframe",
    "strategy_id",
    "parameters",
    "window_config",
    "source",
    "notes",
    "fingerprint",
)
EXTERNAL_CANDIDATE_WINDOW_FIELDS = (
    "train_bars",
    "test_bars",
    "step_bars",
    "holdout_ratio",
    "min_trade_count",
)


def load_external_candidate_file(path: str | Path) -> dict[str, Any]:
    candidate_path = Path(path)
    try:
        if candidate_path.suffix.lower() == ".csv":
            raw_payload = _read_candidate_csv(candidate_path)
        else:
            raw_payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _candidate_parse_report(
            source_path=candidate_path,
            status="SKIPPED",
            message="external candidate input is missing",
            reason_codes=["external_candidate_input_missing"],
            raw_candidates=[],
        )
    except json.JSONDecodeError as exc:
        return _candidate_parse_report(
            source_path=candidate_path,
            status="FAIL",
            message=f"external candidate JSON is invalid: {exc}",
            reason_codes=["external_candidate_input_invalid_json"],
            raw_candidates=[],
        )
    except csv.Error as exc:
        return _candidate_parse_report(
            source_path=candidate_path,
            status="FAIL",
            message=f"external candidate CSV is invalid: {exc}",
            reason_codes=["external_candidate_input_invalid_csv"],
            raw_candidates=[],
        )

    raw_candidates = _extract_raw_candidates(raw_payload)
    return _candidate_parse_report(
        source_path=candidate_path,
        status="PASS",
        message="external candidate input parsed",
        reason_codes=[],
        raw_candidates=raw_candidates,
        raw_payload=raw_payload,
    )


def normalize_external_candidate_payload(
    raw_candidates: list[Any],
    *,
    source_path: str | Path | None = None,
) -> dict[str, Any]:
    valid_candidates: list[dict[str, Any]] = []
    invalid_candidates: list[dict[str, Any]] = []
    for index, raw_candidate in enumerate(raw_candidates, start=1):
        try:
            valid_candidates.append(
                normalize_external_candidate(
                    raw_candidate,
                    raw_index=index,
                    source_path=source_path,
                )
            )
        except ValueError as exc:
            invalid_candidates.append(
                {
                    "raw_index": index,
                    "status": "FAIL",
                    "message": str(exc),
                    "reason_codes": ["invalid_external_candidate"],
                    "raw_candidate": raw_candidate if isinstance(raw_candidate, dict) else None,
                }
            )

    reason_codes: set[str] = set()
    if valid_candidates:
        reason_codes.add("external_candidate_schema_ready")
    if invalid_candidates:
        reason_codes.add("invalid_external_candidates_present")
    if not raw_candidates:
        reason_codes.add("external_candidate_list_empty")

    return {
        "schema_version": EXTERNAL_CANDIDATE_SCHEMA_VERSION,
        "status": "PASS" if valid_candidates and not invalid_candidates else (
            "SKIPPED" if not valid_candidates else "PARTIAL"
        ),
        "candidate_count": len(raw_candidates),
        "valid_candidate_count": len(valid_candidates),
        "invalid_candidate_count": len(invalid_candidates),
        "valid_candidates": valid_candidates,
        "invalid_candidates": invalid_candidates,
        "required_fields": list(EXTERNAL_CANDIDATE_REQUIRED_FIELDS),
        "window_fields": list(EXTERNAL_CANDIDATE_WINDOW_FIELDS),
        "reason_codes": sorted(reason_codes),
    }


def normalize_external_candidate(
    raw_candidate: Any,
    *,
    raw_index: int,
    source_path: str | Path | None = None,
) -> dict[str, Any]:
    if not isinstance(raw_candidate, dict):
        raise ValueError("external_candidate_must_be_object")

    missing_fields = [
        field
        for field in EXTERNAL_CANDIDATE_REQUIRED_FIELDS
        if field not in raw_candidate
    ]
    if missing_fields:
        raise ValueError(
            "external_candidate_missing_required_fields: "
            + ",".join(missing_fields)
        )

    symbol = _required_text(raw_candidate.get("symbol"), "symbol").upper()
    timeframe = _required_text(raw_candidate.get("timeframe"), "timeframe")
    strategy_id = _required_text(raw_candidate.get("strategy_id"), "strategy_id").lower()
    if strategy_id not in set(SUPPORTED_CANDIDATE_STRATEGY_IDS):
        raise ValueError(f"unsupported_candidate_strategy: {strategy_id}")

    parameters = _object_field(raw_candidate.get("parameters"), "parameters")
    normalized_parameters = normalize_candidate_strategy_parameters(
        strategy_id,
        parameters,
    )
    window_config = _normalize_window_config(
        _object_field(raw_candidate.get("window_config"), "window_config")
    )
    source = _normalize_source(raw_candidate.get("source"))
    notes = _required_text(raw_candidate.get("notes"), "notes")
    declared_fingerprint = _required_text(
        raw_candidate.get("fingerprint"),
        "fingerprint",
    )
    fingerprint_payload = {
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy_id": strategy_id,
        "parameters": normalized_parameters,
        "window_config": window_config,
        "source": source,
        "notes": notes,
    }
    computed_fingerprint = _sha256_payload(fingerprint_payload)
    return {
        "schema_version": EXTERNAL_CANDIDATE_SCHEMA_VERSION,
        "raw_index": raw_index,
        "source_path": str(source_path) if source_path is not None else None,
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy_id": strategy_id,
        "parameters": normalized_parameters,
        "window_config": window_config,
        "source": source,
        "notes": notes,
        "fingerprint": declared_fingerprint,
        "computed_fingerprint": computed_fingerprint,
        "fingerprint_verification": {
            "declared_fingerprint": declared_fingerprint,
            "computed_fingerprint": computed_fingerprint,
            "declared_matches_computed": declared_fingerprint == computed_fingerprint,
            "fingerprint_is_external_provenance": declared_fingerprint != computed_fingerprint,
        },
        "strategy_metadata": candidate_strategy_metadata(strategy_id, normalized_parameters),
        "required_bar_count": _required_bar_count(window_config),
        "reason_codes": ["external_candidate_schema_valid"],
    }


def external_candidate_key(candidate: dict[str, Any]) -> str:
    return "|".join(
        [
            str(candidate.get("symbol")),
            str(candidate.get("timeframe")),
            str(candidate.get("strategy_id")),
            str(candidate.get("fingerprint")),
        ]
    )


def external_candidate_version(
    candidate: dict[str, Any],
    *,
    data_fingerprint: dict[str, Any] | None = None,
    phase: str = "confirm",
) -> str:
    data_digest = str((data_fingerprint or {}).get("sha256") or "missing")[:12]
    parameter_digest = _sha256_payload(candidate.get("parameters") or {})[:10]
    window = candidate.get("window_config") or {}
    holdout_token = str(int(round(float(window.get("holdout_ratio", 0.0)) * 100)))
    return _safe_token(
        f"external-public-{str(candidate.get('symbol')).lower()}-{phase}-"
        f"{candidate.get('timeframe')}-{candidate.get('strategy_id')}-"
        f"data-{data_digest}-p{parameter_digest}-"
        f"tr{window.get('train_bars')}-te{window.get('test_bars')}-"
        f"st{window.get('step_bars')}-ho{holdout_token}-"
        f"mt{window.get('min_trade_count')}-"
        f"xfp{str(candidate.get('fingerprint'))[:12]}"
    )


def _candidate_parse_report(
    *,
    source_path: Path,
    status: str,
    message: str,
    reason_codes: list[str],
    raw_candidates: list[Any],
    raw_payload: Any | None = None,
) -> dict[str, Any]:
    normalized = normalize_external_candidate_payload(
        raw_candidates,
        source_path=source_path,
    )
    merged_reason_codes = sorted(set(reason_codes + normalized["reason_codes"]))
    if status == "PASS" and normalized["status"] != "PASS":
        status = normalized["status"]
    return {
        "schema_version": EXTERNAL_CANDIDATE_SCHEMA_VERSION,
        "status": status,
        "message": message,
        "source_path": str(source_path),
        "raw_payload_shape": _payload_shape(raw_payload),
        "candidate_count": normalized["candidate_count"],
        "valid_candidate_count": normalized["valid_candidate_count"],
        "invalid_candidate_count": normalized["invalid_candidate_count"],
        "valid_candidates": normalized["valid_candidates"],
        "invalid_candidates": normalized["invalid_candidates"],
        "required_fields": normalized["required_fields"],
        "window_fields": normalized["window_fields"],
        "reason_codes": merged_reason_codes,
    }


def _extract_raw_candidates(raw_payload: Any) -> list[Any]:
    if isinstance(raw_payload, list):
        return raw_payload
    if isinstance(raw_payload, dict):
        candidates = raw_payload.get("candidates")
        if isinstance(candidates, list):
            return candidates
    return []


def _read_candidate_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    parsed_rows = []
    for row in rows:
        parsed = dict(row)
        for field in ("parameters", "window_config", "source"):
            parsed[field] = _parse_json_field(parsed.get(field), field)
        parsed_rows.append(parsed)
    return parsed_rows


def _parse_json_field(value: Any, field_name: str) -> Any:
    if value is None or value == "":
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise csv.Error(f"{field_name} must contain JSON") from exc


def _normalize_window_config(raw_window: dict[str, Any]) -> dict[str, Any]:
    missing_fields = [
        field for field in EXTERNAL_CANDIDATE_WINDOW_FIELDS if field not in raw_window
    ]
    if missing_fields:
        raise ValueError(
            "external_candidate_window_missing_required_fields: "
            + ",".join(missing_fields)
        )
    train_bars = _positive_int(raw_window.get("train_bars"), "train_bars")
    test_bars = _positive_int(raw_window.get("test_bars"), "test_bars")
    step_bars = _positive_int(raw_window.get("step_bars"), "step_bars")
    min_trade_count = _positive_int(
        raw_window.get("min_trade_count"),
        "min_trade_count",
    )
    holdout_ratio = _bounded_float(
        raw_window.get("holdout_ratio"),
        "holdout_ratio",
        minimum_exclusive=0.0,
        maximum_exclusive=1.0,
    )
    return {
        "train_bars": train_bars,
        "test_bars": test_bars,
        "step_bars": step_bars,
        "holdout_ratio": holdout_ratio,
        "min_trade_count": min_trade_count,
    }


def _required_bar_count(window_config: dict[str, Any]) -> int:
    return int(window_config["train_bars"]) + int(window_config["test_bars"])


def _normalize_source(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    text = _required_text(value, "source")
    return {"kind": "external", "name": text}


def _object_field(value: Any, field_name: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    raise ValueError(f"external_candidate_{field_name}_must_be_object")


def _required_text(value: Any, field_name: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ValueError(f"external_candidate_{field_name}_must_not_be_empty")
    return text


def _positive_int(value: Any, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"external_candidate_{field_name}_must_be_integer") from exc
    if parsed <= 0:
        raise ValueError(f"external_candidate_{field_name}_must_be_positive")
    return parsed


def _bounded_float(
    value: Any,
    field_name: str,
    *,
    minimum_exclusive: float,
    maximum_exclusive: float,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"external_candidate_{field_name}_must_be_number") from exc
    if not minimum_exclusive < parsed < maximum_exclusive:
        raise ValueError(
            f"external_candidate_{field_name}_must_be_between_"
            f"{minimum_exclusive}_and_{maximum_exclusive}"
        )
    return parsed


def _payload_shape(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return {
            "type": "object",
            "keys": sorted(str(key) for key in payload),
            "candidate_count": len(payload.get("candidates") or [])
            if isinstance(payload.get("candidates"), list)
            else 0,
        }
    if isinstance(payload, list):
        return {"type": "list", "candidate_count": len(payload)}
    return {"type": type(payload).__name__, "candidate_count": 0}


def _sha256_payload(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_token(value: Any) -> str:
    token = "".join(
        character if character.isalnum() or character in "_.-" else "_"
        for character in str(value)
    ).strip("_")
    return token or "unknown"
