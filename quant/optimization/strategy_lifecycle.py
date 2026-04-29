from typing import Dict, Optional

from quant.schemas import (
    StrategyDeploymentRecord,
    StrategyLifecycleAction,
    StrategyLifecycleStatus,
    StrategyLifecycleTransition,
    TraceContext,
)


class StrategyLifecycleManager:
    _TRANSITIONS: Dict[
        StrategyLifecycleAction,
        tuple[StrategyLifecycleStatus, StrategyLifecycleStatus],
    ] = {
        StrategyLifecycleAction.START_BACKTEST: (
            StrategyLifecycleStatus.CANDIDATE,
            StrategyLifecycleStatus.BACKTEST,
        ),
        StrategyLifecycleAction.START_PAPER: (
            StrategyLifecycleStatus.BACKTEST,
            StrategyLifecycleStatus.PAPER,
        ),
        StrategyLifecycleAction.APPROVE: (
            StrategyLifecycleStatus.PAPER,
            StrategyLifecycleStatus.APPROVED,
        ),
        StrategyLifecycleAction.DEPLOY: (
            StrategyLifecycleStatus.APPROVED,
            StrategyLifecycleStatus.DEPLOYED,
        ),
        StrategyLifecycleAction.RETIRE: (
            StrategyLifecycleStatus.DEPLOYED,
            StrategyLifecycleStatus.RETIRED,
        ),
        StrategyLifecycleAction.ROLLBACK: (
            StrategyLifecycleStatus.DEPLOYED,
            StrategyLifecycleStatus.ROLLED_BACK,
        ),
    }

    def transition(
        self,
        record: StrategyDeploymentRecord,
        action: StrategyLifecycleAction,
        transition_id: str,
        generated_at: int,
        reason_codes: Optional[list[str]] = None,
        trace: Optional[TraceContext] = None,
    ) -> tuple[StrategyDeploymentRecord, StrategyLifecycleTransition]:
        expected_from, to_status = self._expected_transition(action)
        if record.status != expected_from:
            raise ValueError(
                f"{action.value} requires {expected_from.value}, got {record.status}"
            )

        transition = StrategyLifecycleTransition(
            transition_id=transition_id,
            strategy_id=record.strategy_id,
            version=record.version,
            action=action,
            from_status=expected_from,
            to_status=to_status,
            generated_at=generated_at,
            reason_codes=reason_codes or [],
            deployment_id=record.deployment_id,
            trace=trace or record.trace,
        )
        updated = self._copy_record(record, to_status, action, generated_at)
        return updated, transition

    def promote_from_optimization_queue(
        self,
        queue,
        symbol: str,
        queue_id: str,
        record: StrategyDeploymentRecord,
        transition_id_prefix: str,
        generated_at: int,
        target_status: StrategyLifecycleStatus = StrategyLifecycleStatus.APPROVED,
        trace: Optional[TraceContext] = None,
    ) -> tuple[StrategyDeploymentRecord, list[StrategyLifecycleTransition]]:
        queue_record = queue.get_record(symbol=symbol, queue_id=queue_id)
        if queue_record is None:
            raise KeyError(f"unknown optimization queue record: {symbol}/{queue_id}")
        decision = queue_record.promotion_decision
        if decision is None:
            raise ValueError(
                f"optimization queue record has no promotion decision: {queue_id}"
            )
        if not decision.approved:
            raise ValueError(
                f"promotion decision rejected candidate: {decision.reason_codes}"
            )
        if record.strategy_id != queue_record.candidate.strategy_id:
            raise ValueError("deployment record strategy_id does not match candidate")
        if record.version != queue_record.candidate.version:
            raise ValueError("deployment record version does not match candidate")
        if target_status not in (
            StrategyLifecycleStatus.BACKTEST,
            StrategyLifecycleStatus.PAPER,
            StrategyLifecycleStatus.APPROVED,
            StrategyLifecycleStatus.DEPLOYED,
        ):
            raise ValueError(f"unsupported promotion target status: {target_status}")

        actions = self._actions_between(record.status, target_status)
        transitions: list[StrategyLifecycleTransition] = []
        updated = record

        for index, action in enumerate(actions, start=1):
            updated, transition = self.transition(
                updated,
                action,
                transition_id=f"{transition_id_prefix}-{index:03d}",
                generated_at=generated_at,
                reason_codes=decision.reason_codes,
                trace=trace or decision.trace or queue_record.trace or record.trace,
            )
            transitions.append(transition)

        return updated, transitions

    def _expected_transition(self, action: StrategyLifecycleAction):
        if action not in self._TRANSITIONS:
            raise ValueError(f"unsupported lifecycle action: {action}")
        return self._TRANSITIONS[action]

    def _actions_between(
        self,
        current_status: StrategyLifecycleStatus,
        target_status: StrategyLifecycleStatus,
    ) -> list[StrategyLifecycleAction]:
        promotion_path = [
            (
                StrategyLifecycleStatus.CANDIDATE,
                StrategyLifecycleAction.START_BACKTEST,
                StrategyLifecycleStatus.BACKTEST,
            ),
            (
                StrategyLifecycleStatus.BACKTEST,
                StrategyLifecycleAction.START_PAPER,
                StrategyLifecycleStatus.PAPER,
            ),
            (
                StrategyLifecycleStatus.PAPER,
                StrategyLifecycleAction.APPROVE,
                StrategyLifecycleStatus.APPROVED,
            ),
            (
                StrategyLifecycleStatus.APPROVED,
                StrategyLifecycleAction.DEPLOY,
                StrategyLifecycleStatus.DEPLOYED,
            ),
        ]
        if current_status == target_status:
            return []

        actions = []
        status = current_status
        for from_status, action, to_status in promotion_path:
            if status != from_status:
                continue
            actions.append(action)
            status = to_status
            if status == target_status:
                return actions

        raise ValueError(
            f"cannot promote lifecycle record from {current_status.value} "
            f"to {target_status.value}"
        )

    def _copy_record(
        self,
        record: StrategyDeploymentRecord,
        status: StrategyLifecycleStatus,
        action: StrategyLifecycleAction,
        generated_at: int,
    ) -> StrategyDeploymentRecord:
        payload = record.to_payload()
        payload["status"] = status.value
        if action == StrategyLifecycleAction.DEPLOY:
            payload["deployed_at"] = generated_at
        if status in (
            StrategyLifecycleStatus.RETIRED,
            StrategyLifecycleStatus.ROLLED_BACK,
        ):
            payload["retired_at"] = generated_at
        return StrategyDeploymentRecord.from_payload(payload)
