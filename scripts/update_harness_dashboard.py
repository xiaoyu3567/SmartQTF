import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.qa.layer_contracts import check_layer_contracts
from quant.security import discover_artifact_paths, scan_artifact_paths, scan_payload

HARNESS_DIR = PROJECT_ROOT / "docs" / "harness"
WEB_DIR = HARNESS_DIR / "web"
TASK_SYSTEM = HARNESS_DIR / "task-system.md"
TASK_ARCHIVE_DIR = HARNESS_DIR / "archive"
WORKFLOW_TEST_TODOLIST = HARNESS_DIR / "workflow-test-todolist.md"
WORKFLOW_TEST_TODOLIST_ARCHIVE = TASK_ARCHIVE_DIR / "workflow-test-todolist-archived.md"
WORKFLOW_REVIEW_HTML = WEB_DIR / "workflow-test-review.html"
WORKFLOW_REVIEW_CASES = WEB_DIR / "workflow-test-review-cases.json"
LAYER_TUTOR_HTML = WEB_DIR / "layer-interaction-tutor.html"
LAYER_TUTOR_CASES = WEB_DIR / "layer-interaction-cases.json"
LAYER_TUTOR_JS = WEB_DIR / "layer-interaction-tutor.js"
LAYER_TUTOR_SECTION = "## 4.1 Layer Interaction Tutor 覆盖映射"
CURRENT_STATE = HARNESS_DIR / "current-state.md"
MILESTONES = HARNESS_DIR / "milestones.md"
LOG_DIR = PROJECT_ROOT / "logs" / "harness-heartbeat"
REHEARSAL_DIR = PROJECT_ROOT / "logs" / "production-rehearsals"
PIPELINE_RUN_DIR = PROJECT_ROOT / "logs" / "pipeline-runs"
LIVE_PIPELINE_RUN_DIR = PROJECT_ROOT / "logs" / "live" / "pipeline-runs"
FAULT_DRILL_DIR = PROJECT_ROOT / "logs" / "fault-drills"
RECONCILIATION_DIR = PROJECT_ROOT / "logs" / "reconciliation"
ALERT_ACTION_LOG_CANDIDATES = [
    PROJECT_ROOT / "logs" / "monitoring" / "alert-actions.jsonl",
    PROJECT_ROOT / "logs" / "alert-actions.jsonl",
]
OUTPUT = WEB_DIR / "harness-status.json"
CHATGPT_EXPORT = PROJECT_ROOT / "chatgpt-1777297245583.html"
SECRET_SCAN_MAX_FILES = 500


EXPECTED_DIRECTORIES = [
    {"path": "docs/harness", "layer": "Harness 文档"},
    {"path": "docs/harness/web", "layer": "Harness Dashboard"},
    {"path": "scripts", "layer": "工具脚本"},
    {"path": "config/examples", "layer": "运行配置样例"},
    {"path": "adapters/exchange", "layer": "交易所 Adapter"},
    {"path": "layers/execution", "layer": "Live 执行入口"},
    {"path": "quant/data", "layer": "数据层"},
    {"path": "quant/features", "layer": "特征层"},
    {"path": "quant/regime", "layer": "市场状态层"},
    {"path": "quant/strategy", "layer": "策略层"},
    {"path": "quant/risk", "layer": "风控层"},
    {"path": "quant/account", "layer": "账户/组合层"},
    {"path": "quant/execution", "layer": "执行层"},
    {"path": "quant/logging", "layer": "记录层"},
    {"path": "quant/analytics", "layer": "复盘分析层"},
    {"path": "quant/optimization", "layer": "优化/生命周期层"},
    {"path": "quant/orchestration", "layer": "调度层"},
    {"path": "quant/monitoring", "layer": "监控层"},
    {"path": "quant/config", "layer": "配置层"},
    {"path": "quant/registry", "layer": "注册机制"},
    {"path": "quant/schemas", "layer": "公共 Schema"},
    {"path": "tests", "layer": "端到端测试"},
]


FLOW_STAGES = [
    {
        "id": "data",
        "name": "Data / 数据",
        "paths": ["quant/data/providers", "quant/data/storage.py", "quant/data/sync.py"],
        "depends_on": [],
        "output": "KlineBatch / OI / FundingRate",
    },
    {
        "id": "quality",
        "name": "Data Quality / 数据质量",
        "paths": ["quant/data/quality.py"],
        "depends_on": ["data"],
        "output": "KlineQualityReport",
    },
    {
        "id": "features",
        "name": "Features / 特征",
        "paths": ["quant/features/pipeline.py", "quant/schemas/feature.py"],
        "depends_on": ["quality"],
        "output": "FeatureSnapshot",
    },
    {
        "id": "regime",
        "name": "Regime / 市场状态",
        "paths": ["quant/regime/rule_detector.py", "quant/schemas/regime.py"],
        "depends_on": ["features"],
        "output": "RegimeSnapshot",
    },
    {
        "id": "strategy",
        "name": "Strategy / 策略路由",
        "paths": ["quant/strategy/router.py", "quant/schemas/strategy.py"],
        "depends_on": ["regime"],
        "output": "StrategySignal",
    },
    {
        "id": "decision",
        "name": "Decision / 决策意图",
        "paths": ["quant/schemas/decision.py", "quant/decision/ai_sandbox.py"],
        "depends_on": ["strategy"],
        "output": "DecisionIntent / AIDecisionSuggestion",
    },
    {
        "id": "risk",
        "name": "Risk / 风控",
        "paths": ["quant/risk/risk_manager.py", "quant/schemas/risk.py"],
        "depends_on": ["decision"],
        "output": "RiskDecision / OrderIntent",
    },
    {
        "id": "portfolio",
        "name": "Portfolio / 组合资金",
        "paths": ["quant/account/portfolio_engine.py", "quant/account/capital_allocator.py"],
        "depends_on": ["risk"],
        "output": "CapitalAllocationDecision",
    },
    {
        "id": "execution",
        "name": "Execution / 执行",
        "paths": ["quant/execution/engine.py", "quant/execution/broker.py"],
        "depends_on": ["portfolio"],
        "output": "Order / Fill / BrokerOrderResult",
    },
    {
        "id": "logging",
        "name": "Logging / 记录",
        "paths": ["quant/logging/jsonl.py", "quant/logging/pipeline_report.py", "quant/schemas/logging.py"],
        "depends_on": ["execution"],
        "output": "Decision/Order/Fill JSONL + PipelineReport JSON",
    },
    {
        "id": "analytics",
        "name": "Analytics / 复盘",
        "paths": ["quant/analytics/daily_review.py", "quant/analytics/attribution.py"],
        "depends_on": ["logging"],
        "output": "DailyReviewReport / Attribution",
    },
    {
        "id": "optimization",
        "name": "Optimization / 策略升级",
        "paths": [
            "quant/optimization/daily_review_queue.py",
            "quant/optimization/strategy_versioning.py",
            "quant/optimization/strategy_lifecycle.py",
        ],
        "depends_on": ["analytics"],
        "output": "StrategyPromotionDecision / Lifecycle",
    },
    {
        "id": "orchestration",
        "name": "Orchestration / 调度闭环",
        "paths": ["quant/orchestration/runtime.py", "quant/orchestration/paper.py"],
        "depends_on": ["optimization"],
        "output": "PipelineRunReport / BatchRunReport",
    },
    {
        "id": "monitoring",
        "name": "Monitoring / 监控告警",
        "paths": ["quant/monitoring/alert.py", "quant/schemas/monitoring.py"],
        "depends_on": ["orchestration"],
        "output": "RuntimeHealthSnapshot / HealthAlert",
    },
]


PROJECT_CAPABILITIES = [
    {
        "group": "运行闭环",
        "key": "runtime_loop",
        "capability": "Backtest / Paper / Live 统一入口",
        "mapping": "TradingRuntimeOrchestrator + PaperTradingOrchestrator",
        "paths": ["quant/orchestration/runtime.py", "quant/orchestration/paper.py"],
        "task_ids": [],
        "gap": "统一入口已具备。",
    },
    {
        "group": "运行闭环",
        "key": "ten_minute_scan",
        "capability": "10 分钟扫描全部候选币和持仓币",
        "mapping": "RuntimeConfig scan + UniverseSnapshot + run_symbols + scheduler",
        "paths": [
            "quant/orchestration/runtime.py",
            "quant/orchestration/paper.py",
            "quant/orchestration/scanner.py",
            "quant/data/providers/okx_provider.py",
        ],
        "task_ids": ["H-ORCH-006", "H-ORCH-007"],
        "gap": "已新增配置驱动扫描调度器，并可在显式开启后合并 Universe Snapshot、配置持仓币和账户同步动态持仓币；后续需在真实部署环境验收代理、凭据和账户解析。",
    },
    {
        "group": "数据输入",
        "key": "universe",
        "capability": "交易对 universe 发现与过滤",
        "mapping": "OKX instruments + UniverseSnapshot + RuntimeScanScheduler",
        "paths": [
            "adapters/exchange/okx.py",
            "quant/data/providers/okx_provider.py",
            "quant/config/runtime.py",
            "quant/orchestration/scanner.py",
        ],
        "task_ids": ["H-DATA-007", "H-ORCH-007"],
        "gap": "OKX 可生成可回放 Universe Snapshot，扫描调度器可在显式开启后消费并注入扫描请求；下一步是部署环境只读验收。",
    },
    {
        "group": "数据输入",
        "key": "market_microstructure",
        "capability": "实时成交、订单簿、净流入和长窗口成交回放",
        "mapping": "OKXDataProvider -> public trades/history-trades -> TradeStore -> OrderFlowSnapshot / OrderBookSnapshot / NetflowSnapshot",
        "paths": ["quant/data/providers/okx_provider.py", "adapters/exchange/okx.py", "quant/data/storage.py", "quant/features/indicators/orderflow.py", "quant/schemas/feature.py"],
        "task_ids": ["H-DATA-007", "H-DATA-008", "DATA-AUDIT-001", "DATA-AUDIT-002"],
        "gap": "H-DATA-007/H-DATA-008 已完成 OKX public history pagination、本地 TradeStore 和 incremental sync；DATA-AUDIT-001/002 已补齐 OKXDataProvider 可选本地优先路径与 raw trade fallback 去重一致性。后续可选增强为 SQLite/Parquet 级高性能长期 trade store。",
    },
    {
        "group": "账户组合",
        "key": "account_sync",
        "capability": "真实账户、余额和持仓同步",
        "mapping": "Exchange account parser -> AccountSyncAdapter -> CryptoAccount / PortfolioPositionSnapshot / scan holdings",
        "paths": [
            "quant/account/sync.py",
            "quant/account/exchange_sync.py",
            "quant/schemas/account.py",
            "quant/orchestration/scanner.py",
            "scripts/validate_account_sync.py",
        ],
        "task_ids": ["H-ACCT-001", "H-ACCT-002"],
        "gap": "已新增独立 AccountSync 契约、账户同步器、OKX / Binance 只读账户响应解析器、扫描器动态持仓合并入口和账户同步验收脚本；后续仍需在真实部署环境用显式代理和只读凭据完成外部验收。",
    },
    {
        "group": "决策",
        "key": "ai_decision_advisor",
        "capability": "AI 决策建议入口",
        "mapping": "AI provider/fixture -> AIDecisionAdvisor -> AIDecisionSuggestion -> DecisionIntent candidate",
        "paths": [
            "quant/schemas/decision.py",
            "quant/decision/ai_sandbox.py",
            "quant/decision/ai_advisor.py",
            "scripts/validate_ai_decision_advisor.py",
            "quant/schemas/logging.py",
        ],
        "task_ids": ["H-DECISION-003", "H-DECISION-004"],
        "gap": "已新增 AI 建议沙箱和 advisor 入口，AI 只能生成带 confidence、reason codes 和 trace 的 DecisionIntent 候选；携带 risk/execution/order 指令或上下文漂移会被拒绝，建议可记录并回放，后续仍必须经过 Risk。",
    },
    {
        "group": "风控执行",
        "key": "protective_exit",
        "capability": "止盈止损执行闭环",
        "mapping": "Risk protective exit -> typed exit plan -> Execution",
        "paths": ["quant/risk/risk_manager.py", "quant/execution/engine.py", "quant/schemas/execution.py"],
        "task_ids": ["H-RISK-006"],
        "gap": "风控已输出 typed ProtectiveExitPlan，纸交易执行层可注册、取消、触发并回放保护性退出事件；后续如进入真实 live，还需接交易所原生止盈止损/OCO 能力。",
    },
    {
        "group": "风控执行",
        "key": "live_order_gate",
        "capability": "实盘下单权限闸",
        "mapping": "preflight artifact + allow_live_orders + LiveOrderGate + BrokerExecutionHandler",
        "paths": ["scripts/preflight_live_readiness.py", "quant/orchestration/runtime.py", "quant/schemas/execution.py"],
        "task_ids": ["H-EXEC-020"],
        "gap": "真实 live 下单权限闸已接入；后续真实启用仍需要人工刷新成功 preflight/生产演练产物，并显式设置 allow_live_orders=true。",
    },
    {
        "group": "记录复盘",
        "key": "pipeline_persistence",
        "capability": "PipelineRunReport 稳定落盘",
        "mapping": "RuntimeConfig.logging.pipeline_report_dir -> PipelineReportStore JSON artifact",
        "paths": [
            "quant/schemas/pipeline.py",
            "quant/config/runtime.py",
            "quant/logging/pipeline_report.py",
            "quant/orchestration/runtime.py",
        ],
        "task_ids": ["H-LOG-003"],
        "gap": "已新增 PipelineReportStore，运行时和扫描器会按配置稳定写出单次和批次 Pipeline report，并维护 latest 指针。",
    },
    {
        "group": "学习优化",
        "key": "daily_learning",
        "capability": "每日复盘到优化候选队列",
        "mapping": "DailyReviewReport -> DailyReviewOptimizationPlanner -> SymbolOptimizationQueue -> Lifecycle",
        "paths": [
            "quant/analytics/daily_review.py",
            "quant/optimization/daily_review_queue.py",
            "quant/optimization/symbol_queue.py",
            "quant/optimization/strategy_lifecycle.py",
        ],
        "task_ids": ["H-OPT-004"],
        "gap": "日报分桶已可确定性生成策略候选参数，写入 symbol 级优化队列并触发验证门；后续需要接入真实 OOS/walk-forward/Monte Carlo 验证产物。",
    },
    {
        "group": "验证",
        "key": "layer_contract_check",
        "capability": "层级关系静态校验",
        "mapping": "layer-contracts.md -> import boundary test -> dashboard",
        "paths": [
            "docs/harness/layer-contracts.md",
            "quant/qa/layer_contracts.py",
            "tests/test_layer_contracts.py",
            "scripts/update_harness_dashboard.py",
        ],
        "task_ids": ["H-QA-007"],
        "gap": "层级契约已固化为静态测试，并由 Dashboard 展示违规数量。",
    },
]


PROJECT_GAP_TASKS = [
    {
        "ID": "H-OPT-005",
        "layer": "策略验证产物",
        "status": "BLOCKED",
        "priority": "P1",
        "gap": "真实 OOS / walk-forward / Monte Carlo 验证产物尚未提供，优化候选不能推进生命周期。",
        "suggestion": "生产环境提供真实验证 JSON 产物后，运行 scripts/validate_strategy_validation_artifacts.py 并确认 latest 报告 artifact_count > 0。",
    },
]


def strip_markdown(value):
    value = value.strip()
    value = re.sub(r"`([^`]*)`", r"\1", value)
    return value


def parse_markdown_table(path, expected_first_column):
    rows = []
    in_table = False

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table:
                break
            continue

        cells = [strip_markdown(cell) for cell in stripped.strip("|").split("|")]
        if not cells:
            continue

        if cells[0] == expected_first_column:
            headers = cells
            in_table = True
            continue

        if in_table and set(cells[0]) <= {"-", ":"}:
            continue

        if in_table:
            if len(cells) != len(headers):
                continue
            rows.append(dict(zip(headers, cells)))

    return rows


def parse_tasks_from(path):
    tasks = parse_markdown_table(path, "ID")
    for task in tasks:
        task["area"] = task["ID"].split("-")[1] if "-" in task["ID"] else "OTHER"
    return tasks


def parse_all_task_tables_from(path):
    tasks = []
    headers = None
    in_table = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            in_table = False
            headers = None
            continue
        cells = [strip_markdown(cell) for cell in stripped.strip("|").split("|")]
        if not cells:
            continue
        if cells[0] == "ID":
            headers = cells
            in_table = True
            continue
        if in_table and set(cells[0]) <= {"-", ":"}:
            continue
        if in_table and headers and len(cells) == len(headers):
            task = dict(zip(headers, cells))
            if task.get("ID", "").startswith("H-"):
                task["area"] = task["ID"].split("-")[1] if "-" in task["ID"] else "OTHER"
                tasks.append(task)
    return tasks


def parse_tasks():
    return parse_tasks_from(TASK_SYSTEM)


def parse_archived_tasks():
    if not TASK_ARCHIVE_DIR.exists():
        return []
    tasks = []
    archive_paths = sorted(TASK_ARCHIVE_DIR.glob("task-system-*.md"))
    completed_tasks = TASK_ARCHIVE_DIR / "completed-tasks.md"
    if completed_tasks.exists():
        archive_paths.append(completed_tasks)
    for path in archive_paths:
        parser = parse_all_task_tables_from if path.name == "completed-tasks.md" else parse_tasks_from
        for task in parser(path):
            task["archive_path"] = str(path.relative_to(PROJECT_ROOT))
            tasks.append(task)
    return tasks


def related_tasks(tasks, task_ids):
    task_by_id = {task.get("ID"): task for task in tasks if task.get("ID") in task_ids}
    missing_ids = set(task_ids) - set(task_by_id)
    if missing_ids:
        for task in parse_archived_tasks():
            task_id = task.get("ID")
            if task_id in missing_ids and task_id not in task_by_id:
                task_by_id[task_id] = task
    return [task_by_id[task_id] for task_id in task_ids if task_id in task_by_id]


def workflow_test_plan_summary(tasks, archived_tasks=None):
    archived_tasks = archived_tasks or []
    all_tasks = list(tasks) + list(archived_tasks)
    workflow_tasks = []
    for task in all_tasks:
        task_id = task.get("ID", "")
        if not task_id.startswith("H-QA-"):
            continue
        try:
            number = int(task_id.split("-")[-1])
        except ValueError:
            continue
        if 9 <= number <= 16:
            workflow_tasks.append(task)
    status_counts = {}
    for task in workflow_tasks:
        status = task.get("状态", "TODO")
        status_counts[status] = status_counts.get(status, 0) + 1
    checklist_count = 0
    workflow_todolist_path = WORKFLOW_TEST_TODOLIST if WORKFLOW_TEST_TODOLIST.exists() else WORKFLOW_TEST_TODOLIST_ARCHIVE
    if workflow_todolist_path.exists():
        checklist_count = workflow_todolist_path.read_text(encoding="utf-8").count("- [ ]")
    task_system_text = TASK_SYSTEM.read_text(encoding="utf-8") if TASK_SYSTEM.exists() else ""
    mapped_review_task_ids = [task_id for task_id in ["H-MON-013", "H-QA-020", "H-QA-021", "H-MON-014", "H-MON-015", "H-QA-022", "H-MON-016"] if task_id in task_system_text]
    return {
        "path": str(workflow_todolist_path.relative_to(PROJECT_ROOT)),
        "exists": workflow_todolist_path.exists(),
        "checklist_items": checklist_count,
        "coverage_mapping": {
            "path": str(TASK_SYSTEM.relative_to(PROJECT_ROOT)),
            "section": LAYER_TUTOR_SECTION,
            "exists": LAYER_TUTOR_SECTION in task_system_text,
            "mapped_task_ids": mapped_review_task_ids,
            "done_constraints_present": "### 4.1.1 全局 DONE 约束" in task_system_text,
            "review_rule_present": "### 4.1.3 完成度审查规则" in task_system_text,
            "source_test_tasks_archived": [f"H-QA-{number:03d}" for number in range(9, 17)],
        },
        "task_count": len(workflow_tasks),
        "by_status": status_counts,
        "tasks": [
            {
                "ID": task.get("ID"),
                "状态": task.get("状态"),
                "优先级": task.get("优先级"),
                "任务": task.get("任务"),
                "archive_path": task.get("archive_path"),
            }
            for task in workflow_tasks
        ],
    }


def workflow_test_review_console_summary(tasks):
    case_payload = {}
    case_count = 0
    editable_case_count = 0
    simulators = []
    safety = {
        "static_page": True,
        "network_required": False,
        "real_credentials_required": False,
        "live_orders_allowed": False,
        "local_python_runner_enabled_by_default": False,
    }
    if WORKFLOW_REVIEW_CASES.exists():
        try:
            case_payload = json.loads(WORKFLOW_REVIEW_CASES.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            case_payload = {}
        cases = case_payload.get("cases") if isinstance(case_payload, dict) else []
        if isinstance(cases, list):
            case_count = len(cases)
            editable_case_count = sum(1 for case in cases if case.get("editable"))
            simulators = sorted({case.get("simulator") for case in cases if case.get("simulator")})
            payload_safety = case_payload.get("safety") if isinstance(case_payload, dict) else None
            if isinstance(payload_safety, dict):
                safety.update(payload_safety)
    review_task_ids = ["H-QA-017", "H-MON-011", "H-QA-018", "H-QA-019", "H-MON-012"]
    review_tasks = related_tasks(tasks, review_task_ids)
    return {
        "page": str(WORKFLOW_REVIEW_HTML.relative_to(PROJECT_ROOT)),
        "cases": str(WORKFLOW_REVIEW_CASES.relative_to(PROJECT_ROOT)),
        "page_exists": WORKFLOW_REVIEW_HTML.exists(),
        "cases_exists": WORKFLOW_REVIEW_CASES.exists(),
        "case_count": case_count,
        "editable_case_count": editable_case_count,
        "simulators": simulators,
        "safety": safety,
        "tasks": [
            {
                "ID": task.get("ID"),
                "状态": task.get("状态"),
                "优先级": task.get("优先级"),
                "任务": task.get("任务"),
                "archive_path": task.get("archive_path"),
            }
            for task in review_tasks
        ],
    }


def layer_tutor_simulator_edges():
    if not LAYER_TUTOR_JS.exists():
        return []
    text = LAYER_TUTOR_JS.read_text(encoding="utf-8")
    match = re.search(r"const SIMULATOR_EDGE_KEYS = \[(.*?)\];", text, re.DOTALL)
    if not match:
        return []
    return re.findall(r'"([^"]+->[^"]+)"', match.group(1))


def layer_interaction_tutor_summary(tasks):
    payload = {}
    step_count = 0
    editable_step_count = 0
    simulator_count = 0
    teacher_panel_count = 0
    field_explanation_count = 0
    safety = {
        "static_page": True,
        "network_required": False,
        "real_credentials_required": False,
        "live_orders_allowed": False,
        "python_runner_enabled_by_default": False,
    }
    if LAYER_TUTOR_CASES.exists():
        try:
            payload = json.loads(LAYER_TUTOR_CASES.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        steps = payload.get("steps") if isinstance(payload, dict) else []
        if isinstance(steps, list):
            step_count = len(steps)
            editable_step_count = sum(1 for step in steps if step.get("editable_fields"))
            simulator_count = len({step.get("simulator") for step in steps if step.get("simulator")})
            teacher_panel_count = sum(1 for step in steps if step.get("teacher_explanation"))
            field_explanation_count = sum(len(step.get("field_explanations") or {}) for step in steps)
            payload_safety = payload.get("safety") if isinstance(payload, dict) else None
            if isinstance(payload_safety, dict):
                safety.update(payload_safety)
    simulator_edges = layer_tutor_simulator_edges()
    tutor_task_ids = ["H-MON-013", "H-QA-020", "H-QA-021", "H-MON-014", "H-MON-015", "H-QA-022", "H-MON-016"]
    tutor_tasks = related_tasks(tasks, tutor_task_ids)
    return {
        "page": str(LAYER_TUTOR_HTML.relative_to(PROJECT_ROOT)),
        "cases": str(LAYER_TUTOR_CASES.relative_to(PROJECT_ROOT)),
        "page_exists": LAYER_TUTOR_HTML.exists(),
        "cases_exists": LAYER_TUTOR_CASES.exists(),
        "step_count": step_count,
        "editable_step_count": editable_step_count,
        "simulator_count": simulator_count,
        "simulator_coverage_count": len(simulator_edges),
        "simulator_edges": simulator_edges,
        "teacher_panel_count": teacher_panel_count,
        "field_explanation_count": field_explanation_count,
        "safety": safety,
        "tasks": [
            {
                "ID": task.get("ID"),
                "状态": task.get("状态"),
                "优先级": task.get("优先级"),
                "任务": task.get("任务"),
                "archive_path": task.get("archive_path"),
            }
            for task in tutor_tasks
        ],
    }


def archived_task_summary(archived_tasks):
    summary = summarize_tasks(archived_tasks)
    archives = sorted({task.get("archive_path") for task in archived_tasks if task.get("archive_path")})
    return {
        **summary,
        "archives": archives,
    }


def parse_layers():
    layers = parse_markdown_table(CURRENT_STATE, "层级")
    for layer in layers:
        completion = layer.get("完成度", "0%").replace("%", "").strip()
        try:
            layer["completion_value"] = int(completion)
        except ValueError:
            layer["completion_value"] = 0
    return layers


def summarize_tasks(tasks):
    statuses = ["TODO", "DOING", "BLOCKED", "REVIEW", "DONE"]
    priorities = ["P0", "P1", "P2", "P3"]
    by_status = {status: 0 for status in statuses}
    by_priority = {priority: 0 for priority in priorities}
    by_area = {}

    for task in tasks:
        status = task.get("状态", "TODO")
        priority = task.get("优先级", "P3")
        area = task.get("area", "OTHER")
        by_status[status] = by_status.get(status, 0) + 1
        by_priority[priority] = by_priority.get(priority, 0) + 1
        by_area.setdefault(area, {"total": 0, "done": 0, "doing": 0, "blocked": 0})
        by_area[area]["total"] += 1
        if status == "DONE":
            by_area[area]["done"] += 1
        elif status == "DOING":
            by_area[area]["doing"] += 1
        elif status == "BLOCKED":
            by_area[area]["blocked"] += 1

    total = len(tasks)
    done = by_status.get("DONE", 0)
    completion = round((done / total) * 100, 1) if total else 0.0

    return {
        "total": total,
        "completion": completion,
        "by_status": by_status,
        "by_priority": by_priority,
        "by_area": by_area,
    }


def summarize_layers(layers):
    if not layers:
        return {"average_completion": 0.0, "count": 0}

    average = sum(layer["completion_value"] for layer in layers) / len(layers)
    return {
        "average_completion": round(average, 1),
        "count": len(layers),
    }


def inspect_paths(paths):
    found = []
    missing = []
    for path in paths:
        if (PROJECT_ROOT / path).exists():
            found.append(path)
        else:
            missing.append(path)

    if not missing:
        status = "OK"
    elif found:
        status = "PARTIAL"
    else:
        status = "MISSING"

    return {
        "status": status,
        "found": found,
        "missing": missing,
    }


def directory_health():
    entries = []
    for item in EXPECTED_DIRECTORIES:
        path = item["path"]
        exists = (PROJECT_ROOT / path).is_dir()
        entries.append(
            {
                "path": path,
                "layer": item["layer"],
                "status": "OK" if exists else "MISSING",
            }
        )

    ok = sum(1 for item in entries if item["status"] == "OK")
    missing = len(entries) - ok
    return {
        "entries": entries,
        "summary": {
            "total": len(entries),
            "ok": ok,
            "missing": missing,
            "completion": round((ok / len(entries)) * 100, 1) if entries else 0.0,
            "status": "OK" if missing == 0 else "MISSING",
        },
    }


def flow_validation():
    stages = []
    status_by_id = {}
    for index, stage in enumerate(FLOW_STAGES, start=1):
        path_status = inspect_paths(stage["paths"])
        dependency_status = [
            {
                "id": dependency,
                "status": status_by_id.get(dependency, "MISSING"),
            }
            for dependency in stage["depends_on"]
        ]
        dependency_ok = all(item["status"] in {"OK", "PARTIAL"} for item in dependency_status)

        if path_status["status"] == "OK" and dependency_ok:
            status = "OK"
        elif path_status["status"] == "MISSING":
            status = "MISSING"
        else:
            status = "PARTIAL"

        status_by_id[stage["id"]] = status
        stages.append(
            {
                "order": index,
                "id": stage["id"],
                "name": stage["name"],
                "depends_on": stage["depends_on"],
                "output": stage["output"],
                "status": status,
                "found_paths": path_status["found"],
                "missing_paths": path_status["missing"],
                "dependency_status": dependency_status,
            }
        )

    ok = sum(1 for stage in stages if stage["status"] == "OK")
    partial = sum(1 for stage in stages if stage["status"] == "PARTIAL")
    missing = sum(1 for stage in stages if stage["status"] == "MISSING")
    weighted = ok + partial * 0.5
    return {
        "stages": stages,
        "summary": {
            "total": len(stages),
            "ok": ok,
            "partial": partial,
            "missing": missing,
            "completion": round((weighted / len(stages)) * 100, 1) if stages else 0.0,
            "status": "OK" if missing == 0 else "PARTIAL",
        },
    }


def capability_matrix(tasks):
    task_by_id = {task.get("ID"): task for task in tasks}
    capabilities = []
    for item in PROJECT_CAPABILITIES:
        path_status = inspect_paths(item["paths"])
        tasks_for_layer = []
        for task_id in item.get("task_ids", []):
            task = task_by_id.get(task_id)
            if task:
                tasks_for_layer.append(
                    {
                        "ID": task_id,
                        "状态": task.get("状态", "TODO"),
                        "优先级": task.get("优先级", ""),
                        "任务": task.get("任务", ""),
                    }
                )

        open_tasks = [task for task in tasks_for_layer if task["状态"] != "DONE"]
        if path_status["status"] == "MISSING":
            status = "MISSING"
        elif open_tasks:
            status = "PARTIAL"
        else:
            status = path_status["status"]

        capabilities.append(
            {
                "group": item["group"],
                "key": item["key"],
                "capability": item["capability"],
                "mapping": item["mapping"],
                "status": status,
                "gap": item["gap"],
                "found_paths": path_status["found"],
                "missing_paths": path_status["missing"],
                "tasks": tasks_for_layer,
            }
        )

    ok = sum(1 for item in capabilities if item["status"] == "OK")
    partial = sum(1 for item in capabilities if item["status"] == "PARTIAL")
    missing = sum(1 for item in capabilities if item["status"] == "MISSING")
    weighted = ok + partial * 0.5
    return {
        "items": capabilities,
        "summary": {
            "total": len(capabilities),
            "ok": ok,
            "partial": partial,
            "missing": missing,
            "completion": round((weighted / len(capabilities)) * 100, 1) if capabilities else 0.0,
        },
    }


def gap_tasks(tasks):
    archived_task_by_id = {task.get("ID"): task for task in parse_archived_tasks()}
    task_by_id = {**archived_task_by_id, **{task.get("ID"): task for task in tasks}}
    result = []
    for item in PROJECT_GAP_TASKS:
        task = task_by_id.get(item["ID"], {})
        result.append(
            {
                **item,
                "status": task.get("状态", item["status"]),
                "priority": task.get("优先级", item["priority"]),
                "task": task.get("任务", ""),
                "completion_criteria": task.get("完成标准", ""),
            }
        )
    return result


def layer_contract_validation():
    return check_layer_contracts(PROJECT_ROOT).to_payload()


def chatgpt_export_summary():
    if not CHATGPT_EXPORT.exists():
        return {
            "path": str(CHATGPT_EXPORT.relative_to(PROJECT_ROOT)),
            "exists": False,
            "status": "MISSING",
        }

    text = CHATGPT_EXPORT.read_text(encoding="utf-8")
    exported_match = re.search(r"<strong>Exported:</strong>\s*([^<]+)", text)
    message_count = text.count('class="message ')
    complete_html = "</body></html>" in text[-200:].lower()
    return {
        "path": str(CHATGPT_EXPORT.relative_to(PROJECT_ROOT)),
        "exists": True,
        "status": "OK" if complete_html else "PARTIAL",
        "bytes": CHATGPT_EXPORT.stat().st_size,
        "exported_at": exported_match.group(1).strip() if exported_match else None,
        "message_count": message_count,
        "complete_html": complete_html,
    }


def summarize_project_progress(tasks, layers, directory, flow, capabilities, gaps):
    total_tasks = len(tasks)
    done_tasks = sum(1 for task in tasks if task.get("状态") == "DONE")
    active_tasks = list(tasks)
    active_done = sum(1 for task in active_tasks if task.get("状态") == "DONE")
    active_open = [task for task in active_tasks if task.get("状态") != "DONE"]
    task_completion = round((done_tasks / total_tasks) * 100, 1) if total_tasks else 0.0
    active_task_completion = round((active_done / len(active_tasks)) * 100, 1) if active_tasks else 0.0
    layer_completion = summarize_layers(layers)["average_completion"]
    directory_completion = directory["summary"]["completion"]
    flow_completion = flow["summary"]["completion"]
    capability_completion = capabilities["summary"]["completion"]
    overall = round(
        active_task_completion * 0.30
        + layer_completion * 0.25
        + flow_completion * 0.20
        + directory_completion * 0.15
        + capability_completion * 0.10,
        1,
    )

    open_gaps = [item for item in gaps if item["status"] != "DONE"]
    if overall >= 85:
        phase = "验收交接期"
    elif overall >= 70:
        phase = "生产硬化期"
    elif overall >= 50:
        phase = "闭环强化期"
    else:
        phase = "架构补齐期"

    return {
        "overall_completion": overall,
        "phase": phase,
        "task_completion": task_completion,
        "active_task_completion": active_task_completion,
        "layer_average_completion": layer_completion,
        "directory_completion": directory_completion,
        "flow_completion": flow_completion,
        "capability_completion": capability_completion,
        "total_tasks": total_tasks,
        "done_tasks": done_tasks,
        "active_open_tasks": len(active_open),
        "gap_tasks": len(open_gaps),
        "notes": [
            "当前任务板已归档 DONE 和历史替代阻塞项，只保留当前阻塞任务与 Workflow 测试任务。",
            "历史任务证据见 docs/harness/archive/task-system-completed-2026-04-29.md。",
            "任务完成度表示当前任务板推进度；归档任务单独通过 archived_task_summary 展示。",
        ],
    }


def parse_milestones(tasks):
    task_by_id = {task.get("ID"): task for task in tasks}
    milestones = []
    current = None
    task_pattern = re.compile(r"`(?P<id>H-[A-Z]+-\d+)`")

    for line in MILESTONES.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"##\s+(?P<name>Milestone\s+\d+：.+)", line.strip())
        if heading:
            current = {
                "name": heading.group("name"),
                "status": "UNKNOWN",
                "tasks": [],
            }
            milestones.append(current)
            continue

        if current is None:
            continue

        if line.startswith("状态："):
            current["status"] = line.split("：", 1)[1].strip()
            continue

        match = task_pattern.search(line)
        if match:
            task_id = match.group("id")
            task = task_by_id.get(task_id, {})
            current["tasks"].append(
                {
                    "ID": task_id,
                    "状态": task.get("状态", "UNKNOWN"),
                    "优先级": task.get("优先级", ""),
                    "任务": task.get("任务", strip_markdown(line)),
                }
            )

    for milestone in milestones:
        tasks_in_milestone = milestone["tasks"]
        total = len(tasks_in_milestone)
        done = sum(1 for task in tasks_in_milestone if task["状态"] == "DONE")
        blocked = sum(1 for task in tasks_in_milestone if task["状态"] == "BLOCKED")
        doing = sum(1 for task in tasks_in_milestone if task["状态"] in {"DOING", "REVIEW"})
        milestone["summary"] = {
            "total": total,
            "done": done,
            "doing": doing,
            "blocked": blocked,
            "completion": round((done / total) * 100, 1) if total else 100.0,
        }
        if milestone["status"] == "UNKNOWN":
            if total and done == total:
                milestone["status"] = "DONE"
            elif total == 0 and milestone["summary"]["completion"] == 100.0:
                milestone["status"] = "DONE"
            elif blocked:
                milestone["status"] = "BLOCKED"
            elif done or doing:
                milestone["status"] = "DOING"
            else:
                milestone["status"] = "TODO"

    return milestones


def git_status():
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def parse_heartbeat_task(text):
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "当前任务" not in line:
            continue
        for candidate in lines[index + 1 : index + 6]:
            candidate = candidate.strip()
            if not candidate:
                continue
            match = re.match(r"(?P<id>H-[A-Z]+-\d+)\s*[：:]\s*(?P<name>.+)", candidate)
            if match:
                return {
                    "ID": match.group("id"),
                    "任务": strip_markdown(match.group("name")),
                }
    return None


def latest_actual_task(tasks):
    if not LOG_DIR.exists():
        return None

    task_by_id = {task.get("ID"): task for task in tasks}
    final_files = sorted(
        LOG_DIR.glob("heartbeat-*.final.md"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for final_file in final_files:
        parsed = parse_heartbeat_task(final_file.read_text(encoding="utf-8"))
        if not parsed:
            continue
        task = dict(task_by_id.get(parsed["ID"], {}))
        task.update(parsed)
        task["heartbeat_file"] = str(final_file.relative_to(PROJECT_ROOT))
        task["heartbeat_at"] = datetime.fromtimestamp(final_file.stat().st_mtime, timezone.utc).isoformat()
        return task

    return None


def latest_production_rehearsal():
    if not REHEARSAL_DIR.exists():
        return None

    candidates = [REHEARSAL_DIR / "latest.json"]
    candidates.extend(
        sorted(
            (path for path in REHEARSAL_DIR.glob("*.json") if path.name != "latest.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    )

    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        checks = payload.get("checks") or []
        failures = [check for check in checks if check.get("status") == "FAIL"]
        warnings = [check for check in checks if check.get("status") == "WARN"]
        skipped = [check for check in checks if check.get("status") == "SKIPPED"]
        dry_run_summary = payload.get("dry_run_summary") or {}
        metadata = payload.get("metadata") or {}

        return {
            "path": str(path.relative_to(PROJECT_ROOT)),
            "report_id": payload.get("report_id"),
            "generated_at": payload.get("generated_at"),
            "success": payload.get("success"),
            "check_count": len(checks),
            "failed_count": len(failures),
            "warning_count": len(warnings),
            "skipped_count": len(skipped),
            "failure_categories": sorted(
                {check.get("category", "unknown") for check in failures}
            ),
            "failed_checks": [
                {
                    "name": check.get("name"),
                    "category": check.get("category"),
                    "message": check.get("message"),
                    "source": check.get("source"),
                }
                for check in failures[:6]
            ],
            "dry_run_summary": dry_run_summary,
            "dry_run_status": dry_run_summary.get("status"),
            "next_actions": rehearsal_next_actions(failures, dry_run_summary),
            "safety": {
                "contains_real_credentials": metadata.get("contains_real_credentials"),
                "live_orders_sent": metadata.get("live_orders_sent"),
                "ci_safe": metadata.get("ci_safe"),
                "external_exchange_access": metadata.get("external_exchange_access"),
            },
        }

    return None


def rehearsal_next_actions(failures, dry_run_summary):
    actions = []
    categories = {check.get("category") for check in failures}
    sources = {check.get("source") for check in failures}

    if "qtf_environment" in categories:
        actions.append("激活 QTF conda 环境后重新运行演练。")
    if "credential" in categories or "credentials" in categories:
        actions.append("检查只读凭据环境变量，确认未使用真实写权限凭据。")
    if "connectivity" in sources:
        actions.append("确认代理、DNS 和交易所 public endpoint 连通性。")
    if dry_run_summary.get("status") == "FAIL":
        failed_stages = dry_run_summary.get("failed_stages") or []
        if failed_stages:
            actions.append("检查 dry-run 失败阶段：" + ", ".join(failed_stages) + "。")
        else:
            actions.append("检查 dry-run 报告中的失败原因。")
    if not actions:
        actions.append("继续保持 CI 安全演练产物刷新，并在实盘前执行人工 preflight。")
    return actions


def _read_json_payload(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _latest_json_payload(directory, preferred_names=None):
    preferred_names = preferred_names or ("latest.json", "latest-run.json")
    candidates = [directory / name for name in preferred_names]
    if directory.exists():
        candidates.extend(
            sorted(
                (path for path in directory.glob("*.json") if path.name not in preferred_names),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        )
    seen = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        payload = _read_json_payload(path)
        if payload is not None:
            return path, payload
    return None, None


def _extract_named_payloads(value, key):
    matches = []
    if isinstance(value, dict):
        item = value.get(key)
        if isinstance(item, dict):
            matches.append(item)
        for child in value.values():
            matches.extend(_extract_named_payloads(child, key))
    elif isinstance(value, list):
        for child in value:
            matches.extend(_extract_named_payloads(child, key))
    return matches


def _pipeline_report_payloads():
    reports = []
    for path in [PIPELINE_RUN_DIR / "latest-run.json", LIVE_PIPELINE_RUN_DIR / "latest-run.json"]:
        payload = _read_json_payload(path)
        if payload is None:
            continue
        reports.append(
            {
                "path": path,
                "payload": payload,
            }
        )
    return reports


def _order_lifecycle_dashboard_summary(reports):
    orders = []
    seen_order_keys = set()
    for report in reports:
        payload = report["payload"]
        source_path = _project_relative_or_absolute(report["path"])
        execution_results = _extract_named_payloads(payload, "execution_result")
        for execution_result in execution_results:
            lifecycle = execution_result.get("order_lifecycle") or {}
            safety_flags = execution_result.get("safety_flags") or lifecycle.get("safety_flags") or {}
            lifecycle_state = (
                lifecycle.get("lifecycle_state")
                or execution_result.get("lifecycle_state")
                or execution_result.get("status")
                or "UNKNOWN"
            )
            order_status = execution_result.get("status") or lifecycle.get("order_status") or lifecycle_state
            client_order_id = execution_result.get("client_order_id") or lifecycle.get("client_order_id")
            order_key = (source_path, client_order_id, lifecycle_state, order_status)
            if order_key in seen_order_keys:
                continue
            seen_order_keys.add(order_key)
            orders.append(
                {
                    "source_path": source_path,
                    "run_id": (payload.get("context") or {}).get("run_id"),
                    "client_order_id": client_order_id,
                    "symbol": execution_result.get("symbol") or lifecycle.get("symbol"),
                    "side": execution_result.get("side") or lifecycle.get("side"),
                    "status": order_status,
                    "lifecycle_state": lifecycle_state,
                    "lifecycle_path": lifecycle.get("lifecycle_path") or [],
                    "remaining_qty": execution_result.get("remaining_qty") or lifecycle.get("remaining_qty"),
                    "filled_qty": execution_result.get("filled_qty") or lifecycle.get("filled_qty"),
                    "broker_called": bool(
                        execution_result.get("broker_called") or safety_flags.get("broker_called")
                    ),
                    "live_orders_sent": bool(
                        execution_result.get("live_orders_sent") or safety_flags.get("live_orders_sent")
                    ),
                    "dry_run": bool(execution_result.get("dry_run") or safety_flags.get("dry_run")),
                    "execution_mode": lifecycle.get("execution_mode"),
                }
            )

    state_counts = {}
    for order in orders:
        state = order["lifecycle_state"]
        state_counts[state] = state_counts.get(state, 0) + 1

    terminal_states = {"FILLED", "CANCELLED", "REJECTED", "ERROR", "EXPIRED"}
    recovery_states = {"TIMEOUT", "RETRYING", "RECOVERY", "UNKNOWN"}
    terminal_statuses = {"filled", "cancelled", "rejected", "error", "expired"}
    recovery_statuses = {"timeout", "retrying", "recovery", "unknown"}
    terminal_count = 0
    recovery_count = 0
    open_count = 0
    for order in orders:
        state = str(order.get("lifecycle_state") or "").upper()
        status = str(order.get("status") or "").lower()
        if state in terminal_states or status in terminal_statuses:
            terminal_count += 1
        elif state in recovery_states or status in recovery_statuses:
            recovery_count += 1
        else:
            open_count += 1

    return {
        "source_paths": [_project_relative_or_absolute(report["path"]) for report in reports],
        "report_count": len(reports),
        "order_count": len(orders),
        "state_counts": state_counts,
        "open_order_count": open_count,
        "recovery_order_count": recovery_count,
        "terminal_order_count": terminal_count,
        "broker_called": any(order["broker_called"] for order in orders),
        "live_orders_sent": any(order["live_orders_sent"] for order in orders),
        "latest_orders": orders[:8],
    }


def _runtime_health_dashboard_summary(reports, action_summary):
    health_items = []
    for report in reports:
        payload = report["payload"]
        health = (payload.get("metadata") or {}).get("runtime_health")
        if not isinstance(health, dict):
            continue
        health_items.append(
            {
                "source_path": _project_relative_or_absolute(report["path"]),
                "run_id": health.get("run_id") or (payload.get("context") or {}).get("run_id"),
                "status": health.get("status"),
                "alerts": health.get("alerts") or [],
                "kill_switch_active": bool(health.get("kill_switch_active")),
                "broker_reconciliation_anomalies": int(health.get("broker_reconciliation_anomalies") or 0),
                "order_failure_rate": health.get("order_failure_rate"),
                "risk_rejection_rate": health.get("risk_rejection_rate"),
            }
        )

    return {
        "runtime_health_count": len(health_items),
        "kill_switch_active": any(item["kill_switch_active"] for item in health_items)
        or bool(action_summary.get("kill_switch_active")),
        "safe_mode_active": bool(action_summary.get("safe_mode_active")),
        "pause_new_entries_active": bool(action_summary.get("pause_new_entries_active")),
        "block_execution_active": bool(action_summary.get("block_execution_active")),
        "reduce_exposure_active": bool(action_summary.get("reduce_exposure_active")),
        "broker_reconciliation_anomalies": sum(item["broker_reconciliation_anomalies"] for item in health_items),
        "latest_runtime_health": health_items[:4],
    }


def _reconciliation_dashboard_summary(reports):
    path, payload = _latest_json_payload(RECONCILIATION_DIR, preferred_names=("latest.json",))
    runtime_anomalies = 0
    for report in reports:
        health = (report["payload"].get("metadata") or {}).get("runtime_health") or {}
        runtime_anomalies += int(health.get("broker_reconciliation_anomalies") or 0)

    if payload is None:
        return {
            "source_path": _project_relative_or_absolute(RECONCILIATION_DIR / "latest.json"),
            "exists": False,
            "status": "NO_ARTIFACT" if runtime_anomalies == 0 else "ANOMALY",
            "anomaly_count": runtime_anomalies,
            "drift_count": 0,
            "missing_local_count": 0,
            "missing_broker_count": 0,
            "runtime_anomaly_count": runtime_anomalies,
            "action_counts": {},
            "items": [],
        }

    report = payload.get("report") if isinstance(payload.get("report"), dict) else payload
    items = report.get("items") or []
    drift_count = int(report.get("drift_count") or 0)
    missing_local_count = int(report.get("missing_local_count") or 0)
    missing_broker_count = int(report.get("missing_broker_count") or 0)
    anomaly_count = drift_count + missing_local_count + missing_broker_count
    action_counts = {}
    for item in items:
        action = item.get("action") or "unknown"
        action_counts[action] = action_counts.get(action, 0) + 1

    return {
        "source_path": _project_relative_or_absolute(path),
        "exists": True,
        "status": "ANOMALY" if anomaly_count or runtime_anomalies else "OK",
        "broker_name": report.get("broker_name"),
        "checked_count": report.get("checked_count"),
        "matched_count": report.get("matched_count"),
        "anomaly_count": anomaly_count + runtime_anomalies,
        "drift_count": drift_count,
        "missing_local_count": missing_local_count,
        "missing_broker_count": missing_broker_count,
        "runtime_anomaly_count": runtime_anomalies,
        "action_counts": action_counts,
        "items": items[:8],
    }


def _fault_drill_dashboard_summary():
    path, payload = _latest_json_payload(FAULT_DRILL_DIR, preferred_names=("latest.json",))
    if payload is None:
        return {
            "source_path": _project_relative_or_absolute(FAULT_DRILL_DIR / "latest.json"),
            "exists": False,
            "status": "NO_ARTIFACT",
            "scenario_count": 0,
            "passed_count": 0,
            "failed_count": 0,
            "duplicate_order_guard_active": False,
            "no_duplicate_order": False,
            "broker_called": False,
            "live_orders_sent": False,
        }

    safety = payload.get("safety_assertions") or {}
    return {
        "source_path": _project_relative_or_absolute(path),
        "exists": True,
        "run_id": payload.get("run_id"),
        "generated_at": payload.get("generated_at"),
        "status": payload.get("status"),
        "scenario_count": int(payload.get("scenario_count") or 0),
        "passed_count": int(payload.get("passed_count") or 0),
        "failed_count": int(payload.get("failed_count") or 0),
        "duplicate_order_guard_active": bool(safety.get("all_duplicate_order_guards_active")),
        "no_duplicate_order": bool(safety.get("all_no_duplicate_order")),
        "broker_called": bool(payload.get("broker_called")),
        "live_orders_sent": bool(payload.get("live_orders_sent")),
        "contains_real_credentials": bool(payload.get("contains_real_credentials")),
        "scenarios": [
            {
                "scenario_id": scenario.get("scenario_id"),
                "passed": scenario.get("passed"),
                "alert_codes": [alert.get("code") for alert in scenario.get("alerts", [])],
                "action_types": [action.get("action_type") for action in scenario.get("actions", [])],
            }
            for scenario in (payload.get("scenarios") or [])
        ],
    }


def _alert_actions_from_jsonl():
    actions = []
    source_paths = []
    for path in ALERT_ACTION_LOG_CANDIDATES:
        if not path.exists():
            continue
        source_paths.append(_project_relative_or_absolute(path))
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload["_source_path"] = _project_relative_or_absolute(path)
            actions.append(payload)
    return actions, source_paths


def _alert_actions_from_fault_drill():
    path, payload = _latest_json_payload(FAULT_DRILL_DIR, preferred_names=("latest.json",))
    if payload is None:
        return [], []
    source_path = _project_relative_or_absolute(path)
    actions = []
    for scenario in payload.get("scenarios") or []:
        for action in scenario.get("actions") or []:
            action = dict(action)
            action["_source_path"] = source_path
            action["_scenario_id"] = scenario.get("scenario_id")
            actions.append(action)
    return actions, [source_path]


def _alert_action_dashboard_summary():
    jsonl_actions, jsonl_sources = _alert_actions_from_jsonl()
    drill_actions, drill_sources = _alert_actions_from_fault_drill()
    actions = jsonl_actions + drill_actions
    action_type_counts = {}
    alert_code_counts = {}
    control_flags = {
        "safe_mode_active": False,
        "pause_new_entries_active": False,
        "block_execution_active": False,
        "reduce_exposure_active": False,
        "kill_switch_active": False,
    }
    for action in actions:
        action_type = action.get("action_type") or "unknown"
        alert_code = action.get("alert_code") or "unknown"
        action_type_counts[action_type] = action_type_counts.get(action_type, 0) + 1
        alert_code_counts[alert_code] = alert_code_counts.get(alert_code, 0) + 1
        effect = ((action.get("metadata") or {}).get("control_effect") or {})
        control_flags["safe_mode_active"] = control_flags["safe_mode_active"] or bool(
            effect.get("safe_mode") or action_type == "enter_safe_mode"
        )
        control_flags["pause_new_entries_active"] = control_flags["pause_new_entries_active"] or bool(
            effect.get("pause_new_entries") or action_type == "pause_new_entries"
        )
        control_flags["block_execution_active"] = control_flags["block_execution_active"] or bool(
            effect.get("block_execution") or action_type == "block_execution"
        )
        control_flags["reduce_exposure_active"] = control_flags["reduce_exposure_active"] or bool(
            effect.get("reduce_exposure") or action_type == "reduce_exposure"
        )
        control_flags["kill_switch_active"] = control_flags["kill_switch_active"] or bool(
            effect.get("kill_switch") or action_type == "trigger_kill_switch"
        )

    latest_actions = sorted(
        actions,
        key=lambda action: int(action.get("observed_at") or 0),
        reverse=True,
    )[:10]
    source_paths = sorted(set(jsonl_sources + drill_sources))
    return {
        "source_paths": source_paths,
        "audit_log_count": len(jsonl_sources),
        "fault_drill_embedded": bool(drill_actions),
        "action_count": len(actions),
        "action_type_counts": action_type_counts,
        "alert_code_counts": alert_code_counts,
        "safe_mode_active": control_flags["safe_mode_active"],
        "pause_new_entries_active": control_flags["pause_new_entries_active"],
        "block_execution_active": control_flags["block_execution_active"],
        "reduce_exposure_active": control_flags["reduce_exposure_active"],
        "kill_switch_active": control_flags["kill_switch_active"],
        "broker_called": any(bool(action.get("broker_called")) for action in actions),
        "live_orders_sent": any(bool(action.get("live_orders_sent")) for action in actions),
        "latest_actions": [
            {
                "source_path": action.get("_source_path"),
                "scenario_id": action.get("_scenario_id"),
                "action_type": action.get("action_type"),
                "alert_code": action.get("alert_code"),
                "alert_severity": action.get("alert_severity"),
                "requires_human_ack": action.get("requires_human_ack"),
                "broker_called": action.get("broker_called"),
                "live_orders_sent": action.get("live_orders_sent"),
                "observed_at": action.get("observed_at"),
            }
            for action in latest_actions
        ],
    }


def live_safety_dashboard_summary():
    reports = _pipeline_report_payloads()
    order_lifecycle = _order_lifecycle_dashboard_summary(reports)
    reconciliation = _reconciliation_dashboard_summary(reports)
    fault_drill = _fault_drill_dashboard_summary()
    alert_actions = _alert_action_dashboard_summary()
    runtime_safety = _runtime_health_dashboard_summary(reports, alert_actions)
    return {
        "order_lifecycle": order_lifecycle,
        "reconciliation": reconciliation,
        "alert_actions": alert_actions,
        "runtime_safety": runtime_safety,
        "fault_drill": fault_drill,
        "safety": {
            "network_used": False,
            "broker_called": bool(
                order_lifecycle.get("broker_called")
                or alert_actions.get("broker_called")
                or fault_drill.get("broker_called")
            ),
            "live_orders_sent": bool(
                order_lifecycle.get("live_orders_sent")
                or alert_actions.get("live_orders_sent")
                or fault_drill.get("live_orders_sent")
            ),
            "real_credentials_required": False,
            "contains_real_credentials": bool(fault_drill.get("contains_real_credentials")),
        },
    }


def secret_artifact_scan_summary(current_payload=None):
    roots = [
        PROJECT_ROOT / "logs" / "account-sync-validation",
        PROJECT_ROOT / "logs" / "ai-decision-advisor-validation",
        PROJECT_ROOT / "logs" / "fault-drills",
        PROJECT_ROOT / "logs" / "live",
        PROJECT_ROOT / "logs" / "monitoring",
        PROJECT_ROOT / "logs" / "pipeline-runs",
        PROJECT_ROOT / "logs" / "production-rehearsals",
        PROJECT_ROOT / "logs" / "reconciliation",
        PROJECT_ROOT / "logs" / "strategy-validation-artifacts",
        PROJECT_ROOT / "logs" / "trade-journal",
        OUTPUT,
    ]
    paths, truncated = discover_artifact_paths(roots, max_files=SECRET_SCAN_MAX_FILES)
    artifact_report = scan_artifact_paths(
        paths,
        root=PROJECT_ROOT,
        truncated=truncated,
    )
    current_report = scan_payload(
        current_payload,
        source="current_dashboard_payload",
    ) if current_payload is not None else None
    reports = [artifact_report]
    if current_report is not None:
        reports.append(current_report)
    finding_count = sum(report.finding_count for report in reports)
    status = "FAIL" if finding_count else "PASS"
    return {
        "status": status,
        "success": status == "PASS",
        "finding_count": finding_count,
        "scanned_file_count": len(artifact_report.scanned_files),
        "skipped_file_count": len(artifact_report.skipped_files),
        "truncated": truncated or artifact_report.truncated,
        "max_files": SECRET_SCAN_MAX_FILES,
        "target_roots": [_project_relative_or_absolute(path) for path in roots],
        "artifact_report": artifact_report.to_payload(),
        "current_dashboard_payload": current_report.to_payload() if current_report is not None else None,
        "safety": {
            "network_used": False,
            "broker_called": False,
            "live_orders_sent": False,
            "real_credentials_required": False,
        },
    }


def _project_relative_or_absolute(path):
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def main():
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    tasks = parse_tasks()
    archived_tasks = parse_archived_tasks()
    layers = parse_layers()
    milestones = parse_milestones(tasks)
    directory = directory_health()
    flow = flow_validation()
    capabilities = capability_matrix(tasks)
    gaps = gap_tasks(tasks)
    layer_contracts = layer_contract_validation()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": "SmartQTF",
        "tasks": tasks,
        "archived_tasks": archived_tasks,
        "layers": layers,
        "milestones": milestones,
        "task_summary": summarize_tasks(tasks),
        "current_task_summary": summarize_tasks(tasks),
        "archived_task_summary": archived_task_summary(archived_tasks),
        "workflow_test_plan": workflow_test_plan_summary(tasks, archived_tasks),
        "workflow_test_review_console": workflow_test_review_console_summary(tasks),
        "layer_interaction_tutor": layer_interaction_tutor_summary(tasks),
        "layer_summary": summarize_layers(layers),
        "project_progress": summarize_project_progress(
            tasks,
            layers,
            directory,
            flow,
            capabilities,
            gaps,
        ),
        "directory_health": directory,
        "flow_validation": flow,
        "layer_contract_validation": layer_contracts,
        "capability_matrix": capabilities,
        "gap_tasks": gaps,
        "chatgpt_export": chatgpt_export_summary(),
        "git_status": git_status(),
        "latest_task": latest_actual_task(tasks),
        "latest_rehearsal": latest_production_rehearsal(),
        "live_safety_dashboard": live_safety_dashboard_summary(),
        "sources": {
            "task_system": str(TASK_SYSTEM.relative_to(PROJECT_ROOT)),
            "task_archive_dir": str(TASK_ARCHIVE_DIR.relative_to(PROJECT_ROOT)),
            "workflow_test_todolist": str((WORKFLOW_TEST_TODOLIST if WORKFLOW_TEST_TODOLIST.exists() else WORKFLOW_TEST_TODOLIST_ARCHIVE).relative_to(PROJECT_ROOT)),
            "workflow_test_review_html": str(WORKFLOW_REVIEW_HTML.relative_to(PROJECT_ROOT)),
            "workflow_test_review_cases": str(WORKFLOW_REVIEW_CASES.relative_to(PROJECT_ROOT)),
            "layer_interaction_tutor_html": str(LAYER_TUTOR_HTML.relative_to(PROJECT_ROOT)),
            "layer_interaction_tutor_cases": str(LAYER_TUTOR_CASES.relative_to(PROJECT_ROOT)),
            "current_state": str(CURRENT_STATE.relative_to(PROJECT_ROOT)),
            "milestones": str(MILESTONES.relative_to(PROJECT_ROOT)),
            "chatgpt_export": str(CHATGPT_EXPORT.relative_to(PROJECT_ROOT)),
        },
    }
    payload["secret_artifact_scan"] = secret_artifact_scan_summary(payload)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Dashboard data written to {OUTPUT.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
