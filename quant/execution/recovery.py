from quant.execution.broker import BrokerAdapter
from quant.schemas.enums import OrderStatus, TimeoutFailureKind, TimeoutRecoveryAction
from quant.schemas.execution import BrokerOrderResult, TimeoutRecoveryDecision


def recover_timed_out_order(
    broker: BrokerAdapter,
    local_order: BrokerOrderResult,
    *,
    failure: BaseException | str | None = None,
    recovery_attempt: int = 1,
    max_recovery_attempts: int | None = None,
    retry_after_seconds: int | None = None,
    query_open_orders: bool = True,
) -> TimeoutRecoveryDecision:
    """Resolve an uncertain timeout by querying broker truth without resubmitting."""

    _validate_recovery_budget(recovery_attempt, max_recovery_attempts)
    failure_kind = classify_timeout_failure(failure)

    try:
        broker_order = broker.get_order(local_order.client_order_id)
    except KeyError:
        broker_order = (
            _find_open_order_by_client_order_id(broker, local_order)
            if query_open_orders
            else None
        )
        if broker_order is not None:
            return _decision(
                local_order,
                action=TimeoutRecoveryAction.UPDATE_LOCAL_FROM_BROKER,
                reason="broker_open_order_found_after_timeout",
                status=broker_order.status,
                failure_kind=TimeoutFailureKind.EXCHANGE_RESPONSE_DELAYED,
                recovery_attempt=recovery_attempt,
                max_recovery_attempts=max_recovery_attempts,
                retry_after_seconds=retry_after_seconds,
                recovered_order=broker_order,
            )

        if _has_recovery_budget_remaining(recovery_attempt, max_recovery_attempts):
            return _decision(
                local_order,
                action=TimeoutRecoveryAction.RETRY_RECOVERY_LATER,
                reason="broker_order_missing_recovery_budget_remaining",
                status=OrderStatus.UNKNOWN,
                failure_kind=TimeoutFailureKind.EXCHANGE_RESPONSE_DELAYED,
                recovery_attempt=recovery_attempt,
                max_recovery_attempts=max_recovery_attempts,
                retry_after_seconds=retry_after_seconds,
            )

        return TimeoutRecoveryDecision(
            client_order_id=local_order.client_order_id,
            action=TimeoutRecoveryAction.MARK_UNKNOWN,
            reason="broker_order_missing_after_timeout",
            status=OrderStatus.UNKNOWN,
            failure_kind=TimeoutFailureKind.BROKER_ORDER_MISSING,
            recovery_attempt=recovery_attempt,
            max_recovery_attempts=max_recovery_attempts,
            retry_after_seconds=retry_after_seconds,
            trace=local_order.trace,
        )
    except Exception as exc:
        query_failure_kind = classify_timeout_failure(exc)
        if query_failure_kind == TimeoutFailureKind.UNKNOWN:
            query_failure_kind = TimeoutFailureKind.RECOVERY_QUERY_FAILED

        if (
            max_recovery_attempts is not None
            and not _has_recovery_budget_remaining(recovery_attempt, max_recovery_attempts)
        ):
            return _decision(
                local_order,
                action=TimeoutRecoveryAction.MARK_UNKNOWN,
                reason="broker_query_failed_recovery_budget_exhausted",
                status=OrderStatus.UNKNOWN,
                failure_kind=query_failure_kind,
                error=str(exc),
                recovery_attempt=recovery_attempt,
                max_recovery_attempts=max_recovery_attempts,
                retry_after_seconds=retry_after_seconds,
            )

        return TimeoutRecoveryDecision(
            client_order_id=local_order.client_order_id,
            action=TimeoutRecoveryAction.RETRY_RECOVERY_LATER,
            reason="broker_query_failed_after_timeout",
            status=OrderStatus.UNKNOWN,
            failure_kind=query_failure_kind,
            recovery_attempt=recovery_attempt,
            max_recovery_attempts=max_recovery_attempts,
            retry_after_seconds=retry_after_seconds,
            error=str(exc),
            trace=local_order.trace,
        )

    if failure_kind == TimeoutFailureKind.UNKNOWN:
        failure_kind = TimeoutFailureKind.EXCHANGE_RESPONSE_DELAYED

    return _decision(
        local_order,
        action=TimeoutRecoveryAction.UPDATE_LOCAL_FROM_BROKER,
        reason="broker_order_found_after_timeout",
        status=broker_order.status,
        failure_kind=failure_kind,
        recovery_attempt=recovery_attempt,
        max_recovery_attempts=max_recovery_attempts,
        retry_after_seconds=retry_after_seconds,
        recovered_order=broker_order,
        trace=broker_order.trace or local_order.trace,
    )


def classify_timeout_failure(error: BaseException | str | None) -> TimeoutFailureKind:
    if error is None:
        return TimeoutFailureKind.UNKNOWN
    if isinstance(error, TimeoutError):
        return TimeoutFailureKind.API_TIMEOUT
    if isinstance(error, (ConnectionError, OSError)):
        return TimeoutFailureKind.NETWORK_ERROR

    message = str(error).lower()
    if "timeout" in message or "timed out" in message:
        return TimeoutFailureKind.API_TIMEOUT
    if any(
        marker in message
        for marker in ("network", "connection", "dns", "name resolution", "proxy")
    ):
        return TimeoutFailureKind.NETWORK_ERROR
    if any(marker in message for marker in ("delayed", "eventual", "not yet visible")):
        return TimeoutFailureKind.EXCHANGE_RESPONSE_DELAYED
    return TimeoutFailureKind.UNKNOWN


def _validate_recovery_budget(
    recovery_attempt: int,
    max_recovery_attempts: int | None,
) -> None:
    if recovery_attempt < 1:
        raise ValueError("recovery_attempt must be greater than or equal to 1")
    if max_recovery_attempts is not None and max_recovery_attempts < 1:
        raise ValueError("max_recovery_attempts must be greater than or equal to 1")


def _has_recovery_budget_remaining(
    recovery_attempt: int,
    max_recovery_attempts: int | None,
) -> bool:
    return max_recovery_attempts is not None and recovery_attempt < max_recovery_attempts


def _find_open_order_by_client_order_id(
    broker: BrokerAdapter,
    local_order: BrokerOrderResult,
) -> BrokerOrderResult | None:
    try:
        open_orders = broker.list_open_orders(local_order.symbol)
    except Exception:
        return None

    for broker_order in open_orders:
        if broker_order.client_order_id == local_order.client_order_id:
            return broker_order
    return None


def _decision(
    local_order: BrokerOrderResult,
    *,
    action: TimeoutRecoveryAction,
    reason: str,
    status: OrderStatus,
    failure_kind: TimeoutFailureKind,
    recovery_attempt: int,
    max_recovery_attempts: int | None,
    retry_after_seconds: int | None,
    recovered_order: BrokerOrderResult | None = None,
    error: str | None = None,
    trace=None,
) -> TimeoutRecoveryDecision:
    return TimeoutRecoveryDecision(
        client_order_id=local_order.client_order_id,
        action=action,
        reason=reason,
        status=status,
        failure_kind=failure_kind,
        recovery_attempt=recovery_attempt,
        max_recovery_attempts=max_recovery_attempts,
        recovery_query_attempted=True,
        broker_place_called=False,
        duplicate_order_guard_active=True,
        retry_after_seconds=retry_after_seconds,
        recovered_order=recovered_order,
        error=error,
        trace=trace or local_order.trace,
    )
