import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


REDACTED_VALUE = "***REDACTED***"
ARTIFACT_SUFFIXES = {".json", ".jsonl", ".log", ".md", ".txt"}
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?P<name>api[_-]?key|apikey|secret(?:[_-]?key)?|secretkey|passphrase|token|authorization|signature|private[_-]?key)"
    r"\s*[\"']?\s*[:=]\s*[\"']?(?P<value>[A-Za-z0-9._:/+=@-]{6,})",
    re.IGNORECASE,
)
BEARER_TOKEN_PATTERN = re.compile(
    r"\bBearer\s+(?P<value>[A-Za-z0-9._+/=-]{8,})",
    re.IGNORECASE,
)
PRIVATE_KEY_PATTERN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
SAFE_SECRET_VALUES = {
    "",
    "none",
    "null",
    "missing",
    "not_set",
    "redacted",
    "<redacted>",
    "***redacted***",
    REDACTED_VALUE.lower(),
}


@dataclass(frozen=True)
class SecretFinding:
    source: str
    pattern: str
    field_path: str | None = None
    line_number: int | None = None
    sample: str = REDACTED_VALUE

    def to_payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "pattern": self.pattern,
            "field_path": self.field_path,
            "line_number": self.line_number,
            "sample": self.sample,
        }


@dataclass(frozen=True)
class SecretScanReport:
    status: str
    generated_at: int
    scanned_files: tuple[str, ...] = ()
    skipped_files: tuple[str, ...] = ()
    finding_count: int = 0
    findings: tuple[SecretFinding, ...] = field(default_factory=tuple)
    truncated: bool = False

    @property
    def success(self) -> bool:
        return self.status == "PASS"

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "success": self.success,
            "generated_at": self.generated_at,
            "scanned_files": list(self.scanned_files),
            "skipped_files": list(self.skipped_files),
            "finding_count": self.finding_count,
            "findings": [finding.to_payload() for finding in self.findings],
            "truncated": self.truncated,
            "redaction_value": REDACTED_VALUE,
            "live_orders_sent": False,
            "broker_called": False,
            "network_used": False,
        }


def discover_artifact_paths(
    roots: Iterable[str | Path],
    *,
    suffixes: set[str] | None = None,
    max_files: int = 500,
) -> tuple[list[Path], bool]:
    suffixes = suffixes or ARTIFACT_SUFFIXES
    discovered: list[Path] = []
    truncated = False
    seen: set[Path] = set()

    for root_value in roots:
        root = Path(root_value)
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
        for path in candidates:
            if path.suffix.lower() not in suffixes:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            if len(discovered) >= max_files:
                truncated = True
                break
            seen.add(resolved)
            discovered.append(path)
        if truncated:
            break

    return discovered, truncated


def scan_artifact_paths(
    paths: Iterable[str | Path],
    *,
    root: str | Path | None = None,
    generated_at: int | None = None,
    truncated: bool = False,
) -> SecretScanReport:
    root_path = Path(root) if root is not None else None
    scanned_files: list[str] = []
    skipped_files: list[str] = []
    findings: list[SecretFinding] = []

    for raw_path in paths:
        path = Path(raw_path)
        source = _display_path(path, root_path)
        if not path.exists() or not path.is_file():
            skipped_files.append(source)
            continue
        if path.suffix.lower() not in ARTIFACT_SUFFIXES:
            skipped_files.append(source)
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            skipped_files.append(source)
            continue
        scanned_files.append(source)
        findings.extend(_scan_file_text(source, text, path.suffix.lower()))

    status = "FAIL" if findings else "PASS"
    return SecretScanReport(
        status=status,
        generated_at=int(time.time()) if generated_at is None else generated_at,
        scanned_files=tuple(scanned_files),
        skipped_files=tuple(skipped_files),
        finding_count=len(findings),
        findings=tuple(findings),
        truncated=truncated,
    )


def scan_payload(
    payload: Any,
    *,
    source: str = "payload",
    generated_at: int | None = None,
) -> SecretScanReport:
    findings = tuple(_scan_structured(payload, source=source, field_path="$"))
    status = "FAIL" if findings else "PASS"
    return SecretScanReport(
        status=status,
        generated_at=int(time.time()) if generated_at is None else generated_at,
        finding_count=len(findings),
        findings=findings,
    )


def redact_sensitive_payload(value: Any) -> Any:
    return _redact(value)


def _scan_file_text(source: str, text: str, suffix: str) -> list[SecretFinding]:
    if suffix == ".json":
        try:
            return list(_scan_structured(json.loads(text), source=source, field_path="$"))
        except json.JSONDecodeError:
            return _scan_plain_text(source, text)
    if suffix == ".jsonl":
        return _scan_jsonl(source, text)
    return _scan_plain_text(source, text)


def _scan_jsonl(source: str, text: str) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            findings.extend(_scan_plain_text(source, line, line_number=line_number))
            continue
        findings.extend(
            _scan_structured(
                payload,
                source=source,
                field_path=f"$[line:{line_number}]",
                line_number=line_number,
            )
        )
    return findings


def _scan_structured(
    value: Any,
    *,
    source: str,
    field_path: str,
    line_number: int | None = None,
    key: str | None = None,
) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    if key is not None and _is_sensitive_key(key):
        if not _is_safe_secret_value(value):
            findings.append(
                SecretFinding(
                    source=source,
                    pattern="sensitive_key",
                    field_path=field_path,
                    line_number=line_number,
                )
            )
        return findings

    if isinstance(value, dict):
        for item_key, item_value in value.items():
            child_path = f"{field_path}.{item_key}"
            findings.extend(
                _scan_structured(
                    item_value,
                    source=source,
                    field_path=child_path,
                    line_number=line_number,
                    key=str(item_key),
                )
            )
        return findings

    if isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(
                _scan_structured(
                    item,
                    source=source,
                    field_path=f"{field_path}[{index}]",
                    line_number=line_number,
                )
            )
        return findings

    if isinstance(value, str):
        findings.extend(_scan_text_value(source, value, field_path=field_path, line_number=line_number))
    return findings


def _scan_plain_text(source: str, text: str, *, line_number: int | None = None) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    lines = text.splitlines() or [text]
    for offset, line in enumerate(lines, start=line_number or 1):
        findings.extend(_scan_text_value(source, line, line_number=offset))
    return findings


def _scan_text_value(
    source: str,
    text: str,
    *,
    field_path: str | None = None,
    line_number: int | None = None,
) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    if PRIVATE_KEY_PATTERN.search(text):
        findings.append(
            SecretFinding(
                source=source,
                pattern="private_key_block",
                field_path=field_path,
                line_number=line_number,
            )
        )
    for match in SECRET_ASSIGNMENT_PATTERN.finditer(text):
        if _is_authorization_scheme_only(match.group("name"), match.group("value")):
            continue
        value = match.group("value")
        if not _is_safe_secret_value(value):
            findings.append(
                SecretFinding(
                    source=source,
                    pattern=match.group("name").lower(),
                    field_path=field_path,
                    line_number=line_number,
                )
            )
    for match in BEARER_TOKEN_PATTERN.finditer(text):
        value = match.group("value")
        if not _is_safe_secret_value(value):
            findings.append(
                SecretFinding(
                    source=source,
                    pattern="bearer_token",
                    field_path=field_path,
                    line_number=line_number,
                )
            )
    return findings


def _redact(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _is_sensitive_key(key):
        return value if _is_safe_secret_value(value) else REDACTED_VALUE
    if isinstance(value, dict):
        return {str(item_key): _redact(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(text: str) -> str:
    if PRIVATE_KEY_PATTERN.search(text):
        return REDACTED_VALUE
    redacted = BEARER_TOKEN_PATTERN.sub(f"Bearer {REDACTED_VALUE}", text)
    redacted = SECRET_ASSIGNMENT_PATTERN.sub(_redact_assignment, redacted)
    return redacted


def _redact_assignment(match: re.Match) -> str:
    if _is_authorization_scheme_only(match.group("name"), match.group("value")):
        return match.group(0)
    return f"{match.group('name')}={REDACTED_VALUE}"


def _is_authorization_scheme_only(name: str, value: str) -> bool:
    return name.lower() == "authorization" and value.lower() == "bearer"


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    compact = re.sub(r"[^a-z0-9]+", "", lowered)
    exact = {
        "api_key",
        "apikey",
        "secret",
        "secret_key",
        "secretkey",
        "password",
        "passphrase",
        "token",
        "authorization",
        "signature",
        "private_key",
        "privatekey",
    }
    sensitive_suffixes = (
        "_api_key",
        "_apikey",
        "_secret",
        "_secret_key",
        "_password",
        "_passphrase",
        "_token",
        "_authorization",
        "_signature",
        "_private_key",
    )
    return normalized in exact or compact in exact or normalized.endswith(sensitive_suffixes)


def _is_safe_secret_value(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float)):
        return True
    if isinstance(value, str):
        text = value.strip()
        lowered = text.lower()
        if lowered in SAFE_SECRET_VALUES:
            return True
        if REDACTED_VALUE in text:
            return True
        return False
    if isinstance(value, (list, tuple, set)):
        return len(value) == 0 or all(_is_safe_secret_value(item) for item in value)
    if isinstance(value, dict):
        return len(value) == 0 or all(_is_safe_secret_value(item) for item in value.values())
    return False


def _display_path(path: Path, root: Path | None) -> str:
    if root is not None:
        try:
            return str(path.relative_to(root))
        except ValueError:
            pass
    return str(path)
