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

HARNESS_DIR = PROJECT_ROOT / "docs" / "harness"
WEB_DIR = HARNESS_DIR / "web"
TASK_SYSTEM = HARNESS_DIR / "task-system.md"
CURRENT_STATE = HARNESS_DIR / "current-state.md"
MILESTONES = HARNESS_DIR / "milestones.md"
LOG_DIR = PROJECT_ROOT / "logs" / "harness-heartbeat"
REHEARSAL_DIR = PROJECT_ROOT / "logs" / "production-rehearsals"
OUTPUT = WEB_DIR / "harness-status.json"
CHATGPT_EXPORT = PROJECT_ROOT / "chatgpt-1777297245583.html"


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
        "capability": "实时成交、订单簿和净流入输入",
        "mapping": "OKXDataProvider -> OrderFlowSnapshot / OrderBookSnapshot / NetflowSnapshot",
        "paths": ["quant/data/providers/okx_provider.py", "quant/features/indicators/orderflow.py", "quant/schemas/feature.py"],
        "task_ids": ["H-DATA-008"],
        "gap": "OKX public provider 已接入真实成交、订单簿和成交派生 netflow；后续如需更高实时性，可继续接 WebSocket 增量流或外部资金流数据源。",
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
        "ID": "H-ORCH-006",
        "layer": "运行闭环",
        "status": "DONE",
        "priority": "P1",
        "gap": "已补配置驱动 10 分钟扫描入口，可合并候选币和持仓币并落盘 BatchRunReport。",
        "suggestion": "下一步继续完善具体交易所账户解析，并在真实部署环境验收扫描输入。",
    },
    {
        "ID": "H-ORCH-007",
        "layer": "运行闭环",
        "status": "DONE",
        "priority": "P1",
        "gap": "已补 Universe Snapshot 扫描注入；显式开启后可把 provider 发现的 universe symbol 合并进扫描请求，并写入可回放批次元数据。",
        "suggestion": "下一步在真实部署环境用 OKX public provider 做只读验收，不纳入默认全量 pytest。",
    },
    {
        "ID": "H-DATA-007",
        "layer": "数据输入",
        "status": "DONE",
        "priority": "P1",
        "gap": "已补交易所可交易 symbol universe 的发现、过滤和排序。",
        "suggestion": "下一步验证真实交易所返回的 Universe Snapshot 是否满足生产筛选阈值。",
    },
    {
        "ID": "H-DATA-008",
        "layer": "数据输入",
        "status": "DONE",
        "priority": "P1",
        "gap": "已补 OKX public trades/orderbook 和成交派生 netflow provider 输入。",
        "suggestion": "下一步只在需要更高实时性时接 WebSocket 增量订单簿或外部资金流数据源。",
    },
    {
        "ID": "H-ACCT-001",
        "layer": "账户组合",
        "status": "DONE",
        "priority": "P1",
        "gap": "已补账户余额、权益、持仓和持仓币同步契约，并可同步本地账户、组合持仓快照和扫描列表。",
        "suggestion": "下一步接具体 OKX/Binance 账户响应解析，并在真实凭据环境中做只读验收。",
    },
    {
        "ID": "H-DECISION-003",
        "layer": "决策",
        "status": "DONE",
        "priority": "P2",
        "gap": "已补 AI 决策建议沙箱和 advisor 入口，可安全接收多空、止盈止损、confidence 和 reason code 建议。",
        "suggestion": "下一步在真实部署环境用显式代理、endpoint 和凭据验收 AI provider 调用，输出仍继续经过 Risk/Portfolio/Execution。",
    },
    {
        "ID": "H-RISK-006",
        "layer": "风控执行",
        "status": "DONE",
        "priority": "P1",
        "gap": "已补 typed ProtectiveExitPlan 和纸交易执行层保护性退出触发/回放。",
        "suggestion": "后续进入真实 live 前，继续接交易所原生止盈止损/OCO，并纳入实盘权限闸验收。",
    },
    {
        "ID": "H-EXEC-020",
        "layer": "风控执行",
        "status": "DONE",
        "priority": "P1",
        "gap": "已补真实 live 下单前的强制权限闸。",
        "suggestion": "继续把真实启用流程约束在人工 preflight/生产演练产物、显式 allow_live_orders=true 和 Kill Switch 健康三者全部满足之后。",
    },
    {
        "ID": "H-LOG-003",
        "layer": "记录复盘",
        "status": "DONE",
        "priority": "P1",
        "gap": "已补 PipelineRunReport 和 PipelineBatchRunReport 按配置稳定落盘。",
        "suggestion": "下一步让生产演练、Dashboard 和人工验收流程优先读取 latest-run/latest-batch 指针。",
    },
    {
        "ID": "H-OPT-004",
        "layer": "学习优化",
        "status": "DONE",
        "priority": "P1",
        "gap": "已新增日报到优化候选队列的衔接。",
        "suggestion": "下一步接入生产环境真实 OOS/walk-forward/Monte Carlo 验证产物，避免只依赖日报派生指标。",
    },
    {
        "ID": "H-QA-007",
        "layer": "验证",
        "status": "DONE",
        "priority": "P2",
        "gap": "已补层级关系静态校验，当前会扫描生产源码中的反向 import、策略 Broker 依赖、Risk 下单调用和 Execution Alpha 依赖。",
        "suggestion": "后续新增层级或外部 adapter 时，同步扩展 `DEFAULT_LAYER_CONTRACT_RULES` 并保持 Dashboard 违规数为 0。",
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


def parse_tasks():
    tasks = parse_markdown_table(TASK_SYSTEM, "ID")
    for task in tasks:
        task["area"] = task["ID"].split("-")[1] if "-" in task["ID"] else "OTHER"
    return tasks


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
    task_by_id = {task.get("ID"): task for task in tasks}
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
    active_tasks = [
        task
        for task in tasks
        if not (
            task.get("状态") == "BLOCKED"
            and "取代" in task.get("完成标准", "")
        )
    ]
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
            "任务完成度和结构成熟度分开计算，避免历史任务 DONE 掩盖层级缺口。",
            "H-DATA-005 属于被 H-DATA-006 取代的历史 BLOCKED，不计入 active open。",
            "当前缺口只保留与 SmartQTF 交易闭环直接相关的能力，不再追踪通用任务系统。",
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


def main():
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    tasks = parse_tasks()
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
        "layers": layers,
        "milestones": milestones,
        "task_summary": summarize_tasks(tasks),
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
        "sources": {
            "task_system": str(TASK_SYSTEM.relative_to(PROJECT_ROOT)),
            "current_state": str(CURRENT_STATE.relative_to(PROJECT_ROOT)),
            "milestones": str(MILESTONES.relative_to(PROJECT_ROOT)),
            "chatgpt_export": str(CHATGPT_EXPORT.relative_to(PROJECT_ROOT)),
        },
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Dashboard data written to {OUTPUT.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
