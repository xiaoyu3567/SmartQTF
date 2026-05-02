from quant.execution.state_machine import ExecutionState
from quant.schemas import OrderIntent, OrderLifecycleContract, OrderStatus, PayloadSource, TradeSide


def attach_order_lifecycle_contract(
    execution_result,
    *,
    source,
    order_intent=None,
    state_machine=None,
    dry_run=None,
    metadata=None,
):
    if execution_result is None:
        return None

    result = dict(execution_result)
    contract = build_order_lifecycle_contract(
        result,
        source=source,
        order_intent=order_intent,
        state_machine=state_machine,
        dry_run=dry_run,
        metadata=metadata,
    )
    result["order_lifecycle_contract"] = contract.contract_version
    result["lifecycle_state"] = contract.lifecycle_state
    result["safety_flags"] = dict(contract.safety_flags)
    result["order_lifecycle"] = contract.to_payload()
    return result


def build_order_lifecycle_contract(
    execution_result,
    *,
    source,
    order_intent=None,
    state_machine=None,
    dry_run=None,
    metadata=None,
):
    source = PayloadSource(source)
    order_intent = _coerce_order_intent(order_intent)
    status = _order_status(execution_result.get("status"))
    dry_run_enabled = _dry_run_enabled(execution_result, dry_run)
    lifecycle_state = _lifecycle_state(status)
    filled_qty = _float_value(execution_result.get("filled_qty"), 0.0)
    remaining_qty = _remaining_qty(execution_result, order_intent, filled_qty)
    requested_qty = _requested_qty(execution_result, order_intent, filled_qty, remaining_qty)

    contract_metadata = {
        "order_id": execution_result.get("order_id"),
        "broker_order_id": execution_result.get("broker_order_id"),
        "fill_index": execution_result.get("fill_index"),
    }
    contract_metadata.update(dict(metadata or {}))

    return OrderLifecycleContract(
        source=source,
        execution_mode=_execution_mode(source, dry_run_enabled),
        client_order_id=_client_order_id(execution_result, order_intent),
        symbol=_symbol(execution_result, order_intent),
        side=_trade_side(execution_result, order_intent),
        order_status=status,
        lifecycle_state=lifecycle_state,
        lifecycle_path=_lifecycle_path(status),
        requested_qty=requested_qty,
        filled_qty=filled_qty,
        remaining_qty=remaining_qty,
        order_intent=order_intent,
        transition_audit=_transition_audit(state_machine),
        safety_flags=_safety_flags(source, execution_result, dry_run_enabled),
        metadata=contract_metadata,
        trace=None if order_intent is None else order_intent.trace,
    )


def _coerce_order_intent(order_intent):
    if order_intent is None or isinstance(order_intent, OrderIntent):
        return order_intent
    return OrderIntent.from_payload(order_intent)


def _order_status(status):
    raw = _enum_value(status)
    if raw == "partial":
        raw = OrderStatus.PARTIAL.value
    if raw is None:
        return OrderStatus.UNKNOWN
    try:
        return OrderStatus(str(raw).lower())
    except ValueError:
        return OrderStatus.UNKNOWN


def _lifecycle_state(status):
    return {
        OrderStatus.CREATED: ExecutionState.CREATED,
        OrderStatus.PENDING: ExecutionState.SUBMITTED,
        OrderStatus.ACCEPTED: ExecutionState.SUBMITTED,
        OrderStatus.PARTIAL: ExecutionState.PARTIALLY_FILLED,
        OrderStatus.FILLED: ExecutionState.FILLED,
        OrderStatus.CANCELLED: ExecutionState.CANCELLED,
        OrderStatus.REJECTED: ExecutionState.REJECTED,
        OrderStatus.UNKNOWN: ExecutionState.RECOVERY,
    }[status]


def _lifecycle_path(status):
    if status == OrderStatus.CREATED:
        return [ExecutionState.CREATED]
    if status == OrderStatus.REJECTED:
        return [ExecutionState.CREATED, ExecutionState.REJECTED]
    if status == OrderStatus.CANCELLED:
        return [
            ExecutionState.CREATED,
            ExecutionState.VALIDATED,
            ExecutionState.SUBMITTING,
            ExecutionState.SUBMITTED,
            ExecutionState.CANCELLED,
        ]
    if status == OrderStatus.UNKNOWN:
        return [
            ExecutionState.CREATED,
            ExecutionState.SUBMITTING,
            ExecutionState.TIMEOUT,
            ExecutionState.RECOVERY,
        ]

    path = [
        ExecutionState.CREATED,
        ExecutionState.VALIDATED,
        ExecutionState.SUBMITTING,
        ExecutionState.SUBMITTED,
    ]
    if status == OrderStatus.PARTIAL:
        path.append(ExecutionState.PARTIALLY_FILLED)
    elif status == OrderStatus.FILLED:
        path.append(ExecutionState.FILLED)
    return path


def _execution_mode(source, dry_run_enabled):
    if source == PayloadSource.LIVE and dry_run_enabled:
        return "live_dry_run"
    return source.value


def _dry_run_enabled(execution_result, dry_run):
    if "dry_run" in execution_result:
        return bool(execution_result["dry_run"])
    return bool(dry_run) if dry_run is not None else False


def _safety_flags(source, execution_result, dry_run_enabled):
    return {
        "backtest": source == PayloadSource.BACKTEST,
        "paper": source == PayloadSource.PAPER,
        "live": source == PayloadSource.LIVE,
        "simulated": source in {PayloadSource.BACKTEST, PayloadSource.PAPER},
        "dry_run": dry_run_enabled,
        "broker_called": bool(execution_result.get("broker_called", False)),
        "live_orders_sent": bool(execution_result.get("live_orders_sent", False)),
    }


def _transition_audit(state_machine):
    if state_machine is None or not hasattr(state_machine, "to_audit_log"):
        return []
    return state_machine.to_audit_log()


def _requested_qty(execution_result, order_intent, filled_qty, remaining_qty):
    if order_intent is not None:
        return float(order_intent.quantity)
    requested_qty = execution_result.get("requested_qty")
    if requested_qty is not None:
        return _float_value(requested_qty, 0.0)
    return filled_qty + remaining_qty


def _remaining_qty(execution_result, order_intent, filled_qty):
    remaining_qty = execution_result.get("remaining_qty")
    if remaining_qty is not None:
        return _float_value(remaining_qty, 0.0)
    if order_intent is None:
        return 0.0
    return max(0.0, float(order_intent.quantity) - filled_qty)


def _client_order_id(execution_result, order_intent):
    if order_intent is not None:
        return order_intent.client_order_id
    return str(execution_result.get("client_order_id") or "unknown-client-order")


def _symbol(execution_result, order_intent):
    if order_intent is not None:
        return order_intent.symbol
    return str(execution_result.get("symbol") or "UNKNOWN")


def _trade_side(execution_result, order_intent):
    if order_intent is not None:
        return order_intent.side
    return TradeSide(str(execution_result.get("side")).lower())


def _float_value(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _enum_value(value):
    return value.value if hasattr(value, "value") else value
