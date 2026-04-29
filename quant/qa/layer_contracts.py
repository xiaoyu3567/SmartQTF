import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LayerContractRule:
    rule_id: str
    layer: str
    source_paths: tuple[str, ...]
    description: str
    forbidden_import_prefixes: tuple[str, ...] = ()
    forbidden_call_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class LayerContractViolation:
    rule_id: str
    layer: str
    path: str
    line: int
    kind: str
    target: str
    description: str

    def to_payload(self):
        return {
            "rule_id": self.rule_id,
            "layer": self.layer,
            "path": self.path,
            "line": self.line,
            "kind": self.kind,
            "target": self.target,
            "description": self.description,
        }


@dataclass(frozen=True)
class LayerContractReport:
    rules: tuple[LayerContractRule, ...]
    scanned_files: tuple[str, ...]
    violations: tuple[LayerContractViolation, ...]

    @property
    def passed(self):
        return not self.violations

    def to_payload(self):
        return {
            "summary": {
                "status": "OK" if self.passed else "VIOLATION",
                "rule_count": len(self.rules),
                "scanned_files": len(self.scanned_files),
                "violation_count": len(self.violations),
            },
            "rules": [
                {
                    "rule_id": rule.rule_id,
                    "layer": rule.layer,
                    "source_paths": list(rule.source_paths),
                    "description": rule.description,
                    "forbidden_import_prefixes": list(rule.forbidden_import_prefixes),
                    "forbidden_call_names": list(rule.forbidden_call_names),
                }
                for rule in self.rules
            ],
            "violations": [violation.to_payload() for violation in self.violations],
            "scanned_files": list(self.scanned_files),
        }


DEFAULT_LAYER_CONTRACT_RULES = (
    LayerContractRule(
        rule_id="data-no-upstream-imports",
        layer="Data",
        source_paths=("quant/data",),
        description="Data must not import Features, Strategy, Risk, or Execution.",
        forbidden_import_prefixes=(
            "quant.features",
            "quant.strategy",
            "quant.risk",
            "quant.execution",
        ),
    ),
    LayerContractRule(
        rule_id="features-no-trading-imports",
        layer="Features",
        source_paths=("quant/features",),
        description="Features must not import Strategy, Risk, or Execution.",
        forbidden_import_prefixes=(
            "quant.strategy",
            "quant.risk",
            "quant.execution",
        ),
    ),
    LayerContractRule(
        rule_id="strategy-no-broker-imports",
        layer="Strategy",
        source_paths=("quant/strategy",),
        description="Strategy must not import broker or exchange execution adapters.",
        forbidden_import_prefixes=(
            "quant.execution",
            "layers.execution",
            "adapters.exchange",
        ),
    ),
    LayerContractRule(
        rule_id="risk-no-ordering",
        layer="Risk",
        source_paths=("quant/risk",),
        description="Risk may approve or reject intent, but must not call broker order methods.",
        forbidden_import_prefixes=(
            "quant.execution.broker",
            "layers.execution",
            "adapters.exchange",
        ),
        forbidden_call_names=(
            "place_order",
            "cancel_order",
            "replace_order",
            "on_order_intent",
        ),
    ),
    LayerContractRule(
        rule_id="execution-no-alpha-imports",
        layer="Execution",
        source_paths=("quant/execution", "layers/execution"),
        description="Execution must not import alpha, strategy, regime, or decision layers.",
        forbidden_import_prefixes=(
            "quant.features",
            "quant.strategy",
            "quant.regime",
            "quant.decision",
        ),
    ),
)


def check_layer_contracts(project_root, rules=DEFAULT_LAYER_CONTRACT_RULES):
    project_root = Path(project_root)
    scanned_files = set()
    violations = []

    for rule in rules:
        for source_path in rule.source_paths:
            for path in _iter_python_files(project_root / source_path):
                relative_path = path.relative_to(project_root).as_posix()
                scanned_files.add(relative_path)
                violations.extend(_check_file(project_root, path, rule))

    return LayerContractReport(
        rules=tuple(rules),
        scanned_files=tuple(sorted(scanned_files)),
        violations=tuple(violations),
    )


def _iter_python_files(path):
    if path.is_file() and path.suffix == ".py":
        candidates = [path]
    elif path.is_dir():
        candidates = sorted(path.rglob("*.py"))
    else:
        candidates = []

    for candidate in candidates:
        parts = set(candidate.parts)
        if "tests" in parts or "__pycache__" in parts:
            continue
        if candidate.name.startswith("test_"):
            continue
        yield candidate


def _check_file(project_root, path, rule):
    relative_path = path.relative_to(project_root).as_posix()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
    except SyntaxError as exc:
        return [
            LayerContractViolation(
                rule_id=rule.rule_id,
                layer=rule.layer,
                path=relative_path,
                line=exc.lineno or 0,
                kind="syntax_error",
                target=exc.msg,
                description="Unable to parse source file for layer contract checks.",
            )
        ]

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            matched_prefixes = set()
            for imported in _import_targets(path, project_root, node):
                matched_prefix = _matched_prefix(imported, rule.forbidden_import_prefixes)
                if matched_prefix and matched_prefix not in matched_prefixes:
                    matched_prefixes.add(matched_prefix)
                    violations.append(
                        LayerContractViolation(
                            rule_id=rule.rule_id,
                            layer=rule.layer,
                            path=relative_path,
                            line=node.lineno,
                            kind="import",
                            target=imported,
                            description=rule.description,
                        )
                    )
        elif isinstance(node, ast.Call):
            call_name = _call_name(node.func)
            if call_name in rule.forbidden_call_names:
                violations.append(
                    LayerContractViolation(
                        rule_id=rule.rule_id,
                        layer=rule.layer,
                        path=relative_path,
                        line=node.lineno,
                        kind="call",
                        target=call_name,
                        description=rule.description,
                    )
                )

    return violations


def _import_targets(path, project_root, node):
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]

    module = _resolve_import_from_module(path, project_root, node)
    targets = [module] if module else []
    targets.extend(f"{module}.{alias.name}" if module else alias.name for alias in node.names)
    return targets


def _resolve_import_from_module(path, project_root, node):
    module = node.module or ""
    if node.level == 0:
        return module

    package_parts = _package_parts(path, project_root)
    keep = max(len(package_parts) - node.level + 1, 0)
    resolved_parts = package_parts[:keep]
    if module:
        resolved_parts.extend(module.split("."))
    return ".".join(part for part in resolved_parts if part)


def _package_parts(path, project_root):
    relative = path.relative_to(project_root)
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        return parts[:-1]
    return parts[:-1]


def _matches_any_prefix(value, prefixes):
    return _matched_prefix(value, prefixes) is not None


def _matched_prefix(value, prefixes):
    for prefix in prefixes:
        if value == prefix or value.startswith(f"{prefix}."):
            return prefix
    return None


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""
