from scripts.test_full_pipeline_cli import run_pipeline


def test_demo_pipeline_reads_orchestrator_reports():
    rows, summary = run_pipeline(verbose=False)

    assert summary["report_source"] == "PaperTradingOrchestrator"
    assert summary["shared_report_contract"] == "PipelineRunReport"
    assert summary["reports"] == len(rows)
    assert summary["successful_reports"] == len(rows)
    assert any(row["execution_result_json"] is not None for row in rows)
    assert all("pipeline_report_json" in row for row in rows)
    assert all(row["pipeline_report_json"]["stages"] for row in rows)
