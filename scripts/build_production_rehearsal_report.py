#!/usr/bin/env python
import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.schemas import ProductionRehearsalReport, RehearsalCheckResult, RehearsalCheckStatus
from scripts.diagnose_exchange_connectivity import run_diagnostics
from scripts.preflight_live_readiness import run_preflight


DEFAULT_ARTIFACT_DIR = PROJECT_ROOT / "logs" / "production-rehearsals"


def build_production_rehearsal_report(
    config_path: str | Path,
    *,
    exchanges: list[str] | None = None,
    include_private: bool = False,
    timeout: float = 5.0,
    run_connectivity: bool = True,
    dry_run_report_path: str | Path | None = None,
    require_qtf: bool = True,
    generated_at: int | None = None,
) -> ProductionRehearsalReport:
    generated_at = int(time.time()) if generated_at is None else generated_at
    config_path = Path(config_path)

    preflight_report = run_preflight(config_path, require_qtf=require_qtf)
    checks = _checks_from_preflight(preflight_report)

    connectivity_report: dict[str, Any] | None = None
    if run_connectivity:
        connectivity_report = run_diagnostics(
            exchanges=exchanges,
            include_private=include_private,
            timeout=timeout,
        )
        checks.extend(_checks_from_connectivity(connectivity_report))
    else:
        checks.append(
            RehearsalCheckResult(
                name="connectivity_diagnostics",
                status=RehearsalCheckStatus.SKIPPED,
                category="manual",
                message="connectivity diagnostics were not requested",
                source="connectivity",
            )
        )

    dry_run_summary = _load_dry_run_summary(dry_run_report_path)
    checks.append(_dry_run_check(dry_run_summary))

    success = all(check.status != RehearsalCheckStatus.FAIL for check in checks)
    report = ProductionRehearsalReport(
        report_id=f"production-rehearsal:{generated_at}",
        generated_at=generated_at,
        config_path=str(config_path),
        success=success,
        checks=checks,
        preflight_summary=_summarize_preflight(preflight_report),
        connectivity_summary=_summarize_connectivity(connectivity_report),
        dry_run_summary=dry_run_summary,
        metadata={
            "contains_real_credentials": False,
            "live_orders_sent": False,
            "private_connectivity_requested": include_private,
            "connectivity_requested": run_connectivity,
        },
    )
    return report


def write_rehearsal_artifacts(
    report: ProductionRehearsalReport,
    output_dir: str | Path = DEFAULT_ARTIFACT_DIR,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or _artifact_stem(report.report_id)

    json_path = output_dir / f"{stem}.json"
    markdown_path = output_dir / f"{stem}.md"
    latest_json_path = output_dir / "latest.json"
    latest_markdown_path = output_dir / "latest.md"

    artifact_paths = {
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "latest_json_path": str(latest_json_path),
        "latest_markdown_path": str(latest_markdown_path),
    }
    report.metadata["artifact_paths"] = artifact_paths

    text = json.dumps(report.to_payload(), ensure_ascii=False, indent=2, sort_keys=True)
    markdown = render_rehearsal_markdown(report)

    json_path.write_text(text + "\n", encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    latest_json_path.write_text(text + "\n", encoding="utf-8")
    latest_markdown_path.write_text(markdown, encoding="utf-8")
    return artifact_paths


def render_rehearsal_markdown(report: ProductionRehearsalReport) -> str:
    payload = report.to_payload()
    failed_checks = [check for check in payload["checks"] if check["status"] == "FAIL"]
    warning_checks = [check for check in payload["checks"] if check["status"] == "WARN"]
    skipped_checks = [check for check in payload["checks"] if check["status"] == "SKIPPED"]

    lines = [
        "# SmartQTF Production Rehearsal Report",
        "",
        f"- Report ID: `{payload['report_id']}`",
        f"- Generated At: `{payload['generated_at']}`",
        f"- Config Path: `{payload['config_path']}`",
        f"- Success: `{payload['success']}`",
        f"- Safety: contains_real_credentials=`{payload['metadata'].get('contains_real_credentials')}`, live_orders_sent=`{payload['metadata'].get('live_orders_sent')}`",
        "",
        "## Summary",
        "",
        f"- Checks: `{len(payload['checks'])}`",
        f"- Failed: `{len(failed_checks)}`",
        f"- Warnings: `{len(warning_checks)}`",
        f"- Skipped: `{len(skipped_checks)}`",
        "",
        "## Failure Reasons",
        "",
    ]
    if failed_checks:
        lines.extend(f"- `{check['name']}`: {check['message']}" for check in failed_checks)
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Dry Run Summary",
            "",
        ]
    )
    dry_run_summary = payload.get("dry_run_summary") or {}
    if dry_run_summary:
        for key in sorted(dry_run_summary):
            lines.append(f"- `{key}`: `{dry_run_summary[key]}`")
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Checks",
            "",
            "| Name | Status | Source | Category | Message |",
            "|---|---:|---|---|---|",
        ]
    )
    for check in payload["checks"]:
        lines.append(
            "| {name} | {status} | {source} | {category} | {message} |".format(
                name=_escape_markdown_table(check["name"]),
                status=check["status"],
                source=_escape_markdown_table(check["source"]),
                category=_escape_markdown_table(check["category"]),
                message=_escape_markdown_table(check["message"]),
            )
        )

    lines.append("")
    return "\n".join(lines)


def _checks_from_preflight(report: dict[str, Any]) -> list[RehearsalCheckResult]:
    return [
        RehearsalCheckResult(
            name=f"preflight:{check['name']}",
            status=_status(check["status"]),
            category=check["name"],
            message=check["message"],
            source="preflight",
            details=check.get("details", {}),
        )
        for check in report.get("checks", [])
    ]


def _checks_from_connectivity(report: dict[str, Any]) -> list[RehearsalCheckResult]:
    checks = []
    for check in report.get("checks", []):
        name = f"connectivity:{check['exchange']}:{check['scope']}"
        checks.append(
            RehearsalCheckResult(
                name=name,
                status=_status(check["status"]),
                category=check["category"],
                message=check["message"],
                source="connectivity",
                details={
                    key: value
                    for key, value in check.items()
                    if key not in {"exchange", "scope", "status", "category", "message"}
                },
            )
        )
    return checks


def _load_dry_run_summary(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "status": "SKIPPED",
            "message": "live dry-run report was not supplied; H-EXEC-019 remains the next execution task",
        }

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    stages = payload.get("stages", [])
    return {
        "status": "PASS" if payload.get("success") is True else "FAIL",
        "run_id": (payload.get("context") or {}).get("run_id"),
        "source": (payload.get("context") or {}).get("source"),
        "stage_count": len(stages),
        "failed_stages": [
            stage.get("stage")
            for stage in stages
            if stage.get("status") in {"error", "rejected"}
        ],
    }


def _dry_run_check(summary: dict[str, Any]) -> RehearsalCheckResult:
    status = _status(summary["status"])
    return RehearsalCheckResult(
        name="dry_run:report",
        status=status,
        category="dry_run",
        message=summary.get("message") or "dry-run report summarized",
        source="dry_run",
        details={key: value for key, value in summary.items() if key != "message"},
    )


def _summarize_preflight(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": report.get("success"),
        "failed_count": report.get("failed_count", 0),
        "warning_count": report.get("warning_count", 0),
        "check_count": len(report.get("checks", [])),
    }


def _summarize_connectivity(report: dict[str, Any] | None) -> dict[str, Any]:
    if report is None:
        return {"status": "SKIPPED", "check_count": 0}
    return {
        "success": report.get("success"),
        "failed_count": report.get("failed_count", 0),
        "warning_count": report.get("warning_count", 0),
        "check_count": len(report.get("checks", [])),
        "proxy": report.get("proxy", {}),
    }


def _status(value: str) -> RehearsalCheckStatus:
    return RehearsalCheckStatus(value)


def _artifact_stem(report_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", report_id).strip("-") or "production-rehearsal"


def _escape_markdown_table(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build a replayable SmartQTF production rehearsal report.")
    parser.add_argument("--config", default="config/examples/live-runtime.example.json", help="Runtime config JSON path.")
    parser.add_argument("--exchange", action="append", help="Exchange to diagnose. Defaults to all diagnostics exchanges.")
    parser.add_argument("--include-private", action="store_true", help="Include private read-only connectivity checks.")
    parser.add_argument("--timeout", type=float, default=5.0, help="Connectivity timeout in seconds.")
    parser.add_argument("--skip-connectivity", action="store_true", help="Skip connectivity diagnostics.")
    parser.add_argument("--dry-run-report", help="Optional PipelineRunReport JSON produced by a dry-run.")
    parser.add_argument(
        "--allow-non-qtf",
        action="store_true",
        help="Downgrade missing QTF conda environment from FAIL to WARN in preflight.",
    )
    parser.add_argument("--output", help="Optional JSON output path.")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_ARTIFACT_DIR),
        help="Directory for stable JSON/Markdown rehearsal artifacts.",
    )
    parser.add_argument("--artifact-stem", help="Optional filename stem for JSON/Markdown artifacts.")
    parser.add_argument("--no-artifacts", action="store_true", help="Print only; do not write artifact files.")
    args = parser.parse_args(argv)

    report = build_production_rehearsal_report(
        args.config,
        exchanges=args.exchange,
        include_private=args.include_private,
        timeout=args.timeout,
        run_connectivity=not args.skip_connectivity,
        dry_run_report_path=args.dry_run_report,
        require_qtf=not args.allow_non_qtf,
    )
    if not args.no_artifacts:
        write_rehearsal_artifacts(report, args.output_dir, stem=args.artifact_stem)
    payload = report.to_payload()
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
