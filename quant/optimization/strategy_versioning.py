from typing import Optional

from quant.schemas import (
    StrategyPromotionAction,
    StrategyPromotionDecision,
    StrategyValidationSliceKind,
    StrategyValidationMetrics,
    StrategyVersion,
    StrategyVersionStatus,
    TraceContext,
)


class StrategyVersionGate:
    def __init__(
        self,
        min_trades: int = 1,
        min_net_pnl: float = 0.0,
        max_drawdown: Optional[float] = None,
        min_win_rate: Optional[float] = None,
        require_out_of_sample: bool = False,
        min_out_of_sample_net_pnl: float = 0.0,
        min_walk_forward_windows: int = 0,
        min_walk_forward_pass_rate: Optional[float] = None,
        min_monte_carlo_survival_rate: Optional[float] = None,
    ):
        if min_trades < 0:
            raise ValueError("min_trades must be non-negative")
        if max_drawdown is not None and max_drawdown < 0.0:
            raise ValueError("max_drawdown must be non-negative")
        if min_win_rate is not None and not 0.0 <= min_win_rate <= 1.0:
            raise ValueError("min_win_rate must be between 0.0 and 1.0")
        if min_walk_forward_windows < 0:
            raise ValueError("min_walk_forward_windows must be non-negative")
        if (
            min_walk_forward_pass_rate is not None
            and not 0.0 <= min_walk_forward_pass_rate <= 1.0
        ):
            raise ValueError(
                "min_walk_forward_pass_rate must be between 0.0 and 1.0"
            )
        if (
            min_monte_carlo_survival_rate is not None
            and not 0.0 <= min_monte_carlo_survival_rate <= 1.0
        ):
            raise ValueError(
                "min_monte_carlo_survival_rate must be between 0.0 and 1.0"
            )

        self.min_trades = min_trades
        self.min_net_pnl = min_net_pnl
        self.max_drawdown = max_drawdown
        self.min_win_rate = min_win_rate
        self.require_out_of_sample = require_out_of_sample
        self.min_out_of_sample_net_pnl = min_out_of_sample_net_pnl
        self.min_walk_forward_windows = min_walk_forward_windows
        self.min_walk_forward_pass_rate = min_walk_forward_pass_rate
        self.min_monte_carlo_survival_rate = min_monte_carlo_survival_rate

    def evaluate(
        self,
        candidate: StrategyVersion,
        metrics: StrategyValidationMetrics,
        decision_id: str,
        generated_at: int,
        baseline: Optional[StrategyVersion] = None,
        trace: Optional[TraceContext] = None,
    ) -> StrategyPromotionDecision:
        reason_codes = self._failed_reason_codes(candidate, metrics)
        action = (
            StrategyPromotionAction.REJECT
            if reason_codes
            else StrategyPromotionAction.APPROVE
        )

        if not reason_codes:
            reason_codes = ["promotion_gate_passed"]

        return StrategyPromotionDecision(
            decision_id=decision_id,
            strategy_id=candidate.strategy_id,
            candidate_version=candidate.version,
            baseline_version=baseline.version if baseline else candidate.parent_version,
            action=action,
            generated_at=generated_at,
            reason_codes=reason_codes,
            metrics=metrics,
            trace=trace,
        )

    def next_status(self, decision: StrategyPromotionDecision) -> StrategyVersionStatus:
        if decision.approved:
            return StrategyVersionStatus.APPROVED
        return StrategyVersionStatus.REJECTED

    def _failed_reason_codes(
        self,
        candidate: StrategyVersion,
        metrics: StrategyValidationMetrics,
    ):
        reason_codes = []

        if candidate.status != StrategyVersionStatus.CANDIDATE:
            reason_codes.append("candidate_status_required")
        if metrics.trade_count < self.min_trades:
            reason_codes.append("insufficient_trades")
        if metrics.total_net_pnl < self.min_net_pnl:
            reason_codes.append("net_pnl_below_threshold")
        if self.max_drawdown is not None and metrics.max_drawdown > self.max_drawdown:
            reason_codes.append("drawdown_above_threshold")
        if self.min_win_rate is not None and metrics.win_rate < self.min_win_rate:
            reason_codes.append("win_rate_below_threshold")
        reason_codes.extend(self._anti_overfit_reason_codes(metrics))

        return reason_codes

    def _anti_overfit_reason_codes(self, metrics: StrategyValidationMetrics):
        reason_codes = []
        out_of_sample_slices = [
            item
            for item in metrics.validation_slices
            if item.kind == StrategyValidationSliceKind.OUT_OF_SAMPLE
        ]
        walk_forward_slices = [
            item
            for item in metrics.validation_slices
            if item.kind == StrategyValidationSliceKind.WALK_FORWARD
        ]

        if self.require_out_of_sample or self.min_out_of_sample_net_pnl > 0.0:
            if not out_of_sample_slices:
                reason_codes.append("missing_out_of_sample_validation")
            else:
                out_of_sample_net_pnl = sum(
                    item.total_net_pnl for item in out_of_sample_slices
                )
                if out_of_sample_net_pnl < self.min_out_of_sample_net_pnl:
                    reason_codes.append("out_of_sample_net_pnl_below_threshold")

        if self.min_walk_forward_windows:
            if len(walk_forward_slices) < self.min_walk_forward_windows:
                reason_codes.append("insufficient_walk_forward_windows")

        if self.min_walk_forward_pass_rate is not None:
            if not walk_forward_slices:
                reason_codes.append("missing_walk_forward_validation")
            else:
                passed = sum(
                    1
                    for item in walk_forward_slices
                    if self._validation_slice_passes(item)
                )
                pass_rate = passed / len(walk_forward_slices)
                if pass_rate < self.min_walk_forward_pass_rate:
                    reason_codes.append("walk_forward_pass_rate_below_threshold")

        if self.min_monte_carlo_survival_rate is not None:
            if metrics.monte_carlo_survival_rate is None:
                reason_codes.append("missing_monte_carlo_validation")
            elif (
                metrics.monte_carlo_survival_rate
                < self.min_monte_carlo_survival_rate
            ):
                reason_codes.append("monte_carlo_survival_rate_below_threshold")

        return reason_codes

    def _validation_slice_passes(self, item):
        if item.trade_count < self.min_trades:
            return False
        if item.total_net_pnl < 0.0:
            return False
        if self.max_drawdown is not None and item.max_drawdown > self.max_drawdown:
            return False
        if self.min_win_rate is not None and item.win_rate < self.min_win_rate:
            return False
        return True
