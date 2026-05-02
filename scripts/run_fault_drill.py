import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.monitoring.fault_drill import run_fault_drill, write_fault_drill_report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run fixture-only SmartQTF production fault drills.",
    )
    parser.add_argument(
        "--output-dir",
        default="logs/fault-drills",
        help="Directory where the drill report and latest.json are written.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id. Defaults to fault-drill-<timestamp>.",
    )
    args = parser.parse_args()

    report = run_fault_drill(run_id=args.run_id)
    paths = write_fault_drill_report(report, Path(args.output_dir))
    print(
        json.dumps(
            {
                "status": report.status,
                "run_id": report.run_id,
                "scenario_count": report.scenario_count,
                "passed_count": report.passed_count,
                "failed_count": report.failed_count,
                "broker_called": report.broker_called,
                "live_orders_sent": report.live_orders_sent,
                **paths,
            },
            sort_keys=True,
        )
    )
    return 0 if report.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
