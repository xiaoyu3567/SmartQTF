import json
from pathlib import Path


class PipelineReportStore:
    def __init__(self, report_dir):
        self.report_dir = Path(report_dir)

    @property
    def latest_run_path(self):
        return self.report_dir / "latest-run.json"

    @property
    def latest_batch_path(self):
        return self.report_dir / "latest-batch.json"

    def run_report_path(self, run_id):
        return self.report_dir / f"{self._safe_name(run_id)}.json"

    def batch_report_path(self, batch_id):
        return self.report_dir / f"{self._safe_name(batch_id)}.json"

    def write_run_report(self, report):
        report_path = self.run_report_path(report.context.run_id)
        report = self._with_artifact_metadata(
            report,
            "run",
            report_path,
            self.latest_run_path,
        )
        self._write_payload(report_path, report)
        self._write_payload(self.latest_run_path, report)
        return report

    def write_batch_report(self, batch):
        report_path = self.batch_report_path(batch.batch_id)
        batch = self._with_artifact_metadata(
            batch,
            "batch",
            report_path,
            self.latest_batch_path,
        )
        self._write_payload(report_path, batch)
        self._write_payload(self.latest_batch_path, batch)
        return batch

    def _with_artifact_metadata(self, report, report_type, report_path, latest_path):
        metadata = dict(report.metadata)
        metadata["pipeline_report_artifact"] = {
            "type": report_type,
            "format": "json",
            "report_path": str(report_path),
            "latest_report_path": str(latest_path),
        }
        return self._copy_model(report, {"metadata": metadata})

    def _write_payload(self, path, report):
        self.report_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report.to_payload(), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    @staticmethod
    def _copy_model(model, update):
        if hasattr(model, "model_copy"):
            return model.model_copy(update=update)
        return model.copy(update=update)

    @staticmethod
    def _safe_name(value):
        return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in str(value))
