#!/usr/bin/env python
"""Watch heartbeat logs from a separate terminal and print a safe summary."""

from __future__ import annotations

import argparse
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs" / "harness-heartbeat"
ERROR_RE = re.compile(
    r"\b(ERROR|Traceback|Exception|failed|failure|Dashboard refresh failed)\b",
    re.IGNORECASE,
)
HEARTBEAT_SECTION_RE = re.compile(r"^(当前任务|本轮进展|已运行检查|阻塞事项|下一步)：?\s*$")


@dataclass
class HeartbeatSummary:
    log_path: Path | None = None
    final_path: Path | None = None
    status: str = "WAITING"
    current_task: str = "waiting for heartbeat output"
    next_step: str = "waiting for heartbeat final message"
    final_message: list[str] = field(default_factory=list)
    tools: deque[str] = field(default_factory=lambda: deque(maxlen=8))
    errors: deque[str] = field(default_factory=lambda: deque(maxlen=8))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--tail-lines", type=int, default=2500)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)

    log_dir = Path(args.log_dir)
    last_render = ""
    while True:
        summary = summarize_latest(log_dir, args.tail_lines)
        rendered = render_summary(summary)
        if rendered != last_render:
            print(rendered, flush=True)
            last_render = rendered
        if args.once:
            return 0
        time.sleep(max(args.interval, 0.2))


def summarize_latest(log_dir: Path, tail_lines: int) -> HeartbeatSummary:
    log_path = latest_file(log_dir, "*.log")
    if log_path is None:
        return HeartbeatSummary(status="NO LOG", current_task=f"no heartbeat log in {log_dir}")

    final_path = log_path.with_suffix(".final.md")
    summary = HeartbeatSummary(log_path=log_path, final_path=final_path if final_path.exists() else None)
    lines = read_tail(log_path, tail_lines)
    parse_log_lines(lines, summary)
    if final_path.exists():
        summary.final_message = safe_read_lines(final_path)
        parse_final_message(summary.final_message, summary)
    return summary


def parse_log_lines(lines: list[str], summary: HeartbeatSummary) -> None:
    pending_exec = False
    pending_tool: str | None = None
    for raw_line in lines:
        line = raw_line.rstrip("\n")
        if line.startswith("===== Heartbeat"):
            if "started" in line and summary.status == "WAITING":
                summary.status = "RUNNING"
            elif "completed" in line:
                summary.status = "COMPLETED"
            elif "failed" in line:
                summary.status = "FAILED"
            elif "idle-stopped" in line:
                summary.status = "IDLE STOPPED"
        elif line == "exec":
            pending_exec = True
            continue
        elif pending_exec and line:
            pending_tool = line
            pending_exec = False
            summary.tools.append(f"RUN  {shorten(line, 180)}")
            continue
        elif pending_tool and " succeeded in " in line:
            summary.tools.append(f"OK   {shorten(pending_tool, 180)}")
            pending_tool = None
            continue
        elif pending_tool and " failed in " in line:
            summary.tools.append(f"FAIL {shorten(pending_tool, 180)}")
            summary.errors.append(shorten(line, 220))
            pending_tool = None
            continue

        if looks_like_error(line):
            summary.errors.append(shorten(line, 220))

    parse_embedded_final(lines, summary)


def parse_embedded_final(lines: list[str], summary: HeartbeatSummary) -> None:
    start_index = None
    for index, line in enumerate(lines):
        if line.startswith("[Heartbeat "):
            start_index = index
    if start_index is not None:
        final_lines: list[str] = []
        for line in lines[start_index:]:
            if final_lines and (
                line.startswith("Refreshing harness dashboard data...")
                or line.startswith("===== Heartbeat")
            ):
                break
            final_lines.append(line)
        summary.final_message = final_lines
        parse_final_message(final_lines, summary)


def parse_final_message(lines: list[str], summary: HeartbeatSummary) -> None:
    section: str | None = None
    sections: dict[str, list[str]] = {}
    for line in lines:
        match = HEARTBEAT_SECTION_RE.match(line.strip())
        if match:
            section = match.group(1)
            sections.setdefault(section, [])
        elif section is not None:
            sections.setdefault(section, []).append(line.strip())

    current_task = first_non_empty(sections.get("当前任务", []))
    if current_task:
        summary.current_task = current_task
    next_step = first_non_empty(sections.get("下一步", []))
    if next_step:
        summary.next_step = next_step


def render_summary(summary: HeartbeatSummary) -> str:
    lines = [
        "",
        "===== Harness Heartbeat Summary =====",
        f"Status: {summary.status}",
        f"Log: {display_path(summary.log_path)}",
        f"Final: {display_path(summary.final_path)}",
        f"Current task: {summary.current_task}",
        f"Next step: {summary.next_step}",
        "",
        "Tool output:",
    ]
    lines.extend(f"  {line}" for line in (list(summary.tools) or ["waiting for tool output"]))
    lines.append("")
    lines.append("Errors:")
    lines.extend(f"  {line}" for line in (list(summary.errors) or ["none"]))
    lines.append("")
    lines.append("Final message:")
    for line in preview_final(summary.final_message):
        lines.append(f"  {line}")
    lines.append("=====================================")
    return "\n".join(lines)


def preview_final(lines: list[str]) -> list[str]:
    compact = [line for line in lines if line.strip()]
    if not compact:
        return ["waiting for final message"]
    if len(compact) <= 8:
        return [shorten(line, 220) for line in compact]
    return [shorten(line, 220) for line in compact[:4] + ["..."] + compact[-3:]]


def latest_file(path: Path, pattern: str) -> Path | None:
    try:
        candidates = [candidate for candidate in path.glob(pattern) if candidate.is_file()]
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate.stat().st_mtime)


def read_tail(path: Path, max_lines: int) -> list[str]:
    lines = safe_read_lines(path)
    return lines[-max_lines:]


def safe_read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def first_non_empty(lines: list[str]) -> str | None:
    for line in lines:
        if line.strip():
            return shorten(line.strip(), 220)
    return None


def looks_like_error(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith(("+", "-", "{", "}", '"')):
        return False
    return bool(ERROR_RE.search(stripped))


def shorten(value: str, max_chars: int) -> str:
    return value if len(value) <= max_chars else value[: max_chars - 3] + "..."


def display_path(path: Path | None) -> str:
    if path is None:
        return "not available yet"
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
