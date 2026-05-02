from dataclasses import dataclass
from copy import deepcopy

from quant.schemas import StrategySignal


@dataclass(frozen=True)
class StatelessStrategyValidationResult:
    strategy_id: str
    passed: bool
    errors: tuple[str, ...] = ()
    signal: StrategySignal | None = None


class StatelessStrategyValidator:
    forbidden_attributes = frozenset(
        {
            "signal_buffer",
            "generated_signal_indices",
            "executed_signal_indices",
            "broker",
            "broker_adapter",
            "execution",
            "execution_engine",
            "risk",
            "risk_manager",
        }
    )

    def validate(self, strategy, features, index) -> StatelessStrategyValidationResult:
        errors = []
        strategy_id = getattr(strategy, "strategy_id", strategy.__class__.__name__)

        missing = [
            name
            for name in ("strategy_id", "strategy_version", "generate_signal")
            if not hasattr(strategy, name)
        ]
        if missing:
            errors.append("missing required strategy attributes: " + ", ".join(missing))

        forbidden = sorted(self.forbidden_attributes.intersection(vars(strategy)))
        if forbidden:
            errors.append("strategy holds forbidden cross-layer/state attributes: " + ", ".join(forbidden))

        signal = None
        if not missing and not forbidden:
            before = deepcopy(vars(strategy))
            try:
                signal = strategy.generate_signal(features, index)
            except Exception as exc:
                errors.append(f"generate_signal raised {exc.__class__.__name__}: {exc}")
                signal = None
            after = vars(strategy)

            if before != after:
                errors.append("generate_signal mutated strategy instance state")

            if signal is not None and not isinstance(signal, StrategySignal):
                errors.append("generate_signal must return StrategySignal or None")
                signal = None

        return StatelessStrategyValidationResult(
            strategy_id=strategy_id,
            passed=not errors,
            errors=tuple(errors),
            signal=signal if not errors else None,
        )


def validate_stateless_strategy(strategy, features, index):
    return StatelessStrategyValidator().validate(strategy, features, index)
