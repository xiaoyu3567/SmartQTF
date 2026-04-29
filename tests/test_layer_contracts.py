from pathlib import Path

from quant.qa.layer_contracts import check_layer_contracts


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_current_codebase_respects_layer_contracts():
    report = check_layer_contracts(PROJECT_ROOT)

    assert report.passed, [violation.to_payload() for violation in report.violations]
    assert report.scanned_files


def test_layer_contract_check_reports_forbidden_imports(tmp_path):
    bad_data_file = tmp_path / "quant" / "data" / "bad_provider.py"
    bad_data_file.parent.mkdir(parents=True)
    bad_data_file.write_text(
        "from quant.strategy.ma_crossover import MACrossoverStrategy\n",
        encoding="utf-8",
    )

    report = check_layer_contracts(tmp_path)

    assert not report.passed
    assert report.violations[0].rule_id == "data-no-upstream-imports"
    assert report.violations[0].path == "quant/data/bad_provider.py"
    assert report.violations[0].target.startswith("quant.strategy")


def test_layer_contract_check_reports_risk_order_calls(tmp_path):
    risk_file = tmp_path / "quant" / "risk" / "bad_risk.py"
    risk_file.parent.mkdir(parents=True)
    risk_file.write_text(
        "def evaluate(broker, request):\n"
        "    return broker.place_order(request)\n",
        encoding="utf-8",
    )

    report = check_layer_contracts(tmp_path)

    assert not report.passed
    violation = report.violations[0]
    assert violation.rule_id == "risk-no-ordering"
    assert violation.kind == "call"
    assert violation.target == "place_order"
