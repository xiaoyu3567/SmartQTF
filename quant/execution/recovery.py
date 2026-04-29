from quant.execution.broker import BrokerAdapter
from quant.schemas.enums import OrderStatus, TimeoutRecoveryAction
from quant.schemas.execution import BrokerOrderResult, TimeoutRecoveryDecision


def recover_timed_out_order(
    broker: BrokerAdapter,
    local_order: BrokerOrderResult,
) -> TimeoutRecoveryDecision:
    """Resolve an uncertain timeout by querying broker truth without resubmitting."""

    try:
        broker_order = broker.get_order(local_order.client_order_id)
    except KeyError:
        return TimeoutRecoveryDecision(
            client_order_id=local_order.client_order_id,
            action=TimeoutRecoveryAction.MARK_UNKNOWN,
            reason="broker_order_missing_after_timeout",
            status=OrderStatus.UNKNOWN,
            trace=local_order.trace,
        )
    except Exception as exc:
        return TimeoutRecoveryDecision(
            client_order_id=local_order.client_order_id,
            action=TimeoutRecoveryAction.RETRY_RECOVERY_LATER,
            reason="broker_query_failed_after_timeout",
            status=OrderStatus.UNKNOWN,
            error=str(exc),
            trace=local_order.trace,
        )

    return TimeoutRecoveryDecision(
        client_order_id=local_order.client_order_id,
        action=TimeoutRecoveryAction.UPDATE_LOCAL_FROM_BROKER,
        reason="broker_order_found_after_timeout",
        status=broker_order.status,
        recovered_order=broker_order,
        trace=broker_order.trace or local_order.trace,
    )
