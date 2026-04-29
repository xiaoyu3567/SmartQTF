from abc import ABC, abstractmethod

from quant.schemas import StrategySignal


class Strategy(ABC):
    strategy_id: str
    strategy_version: str

    @abstractmethod
    def generate_signal(self, features, index) -> StrategySignal | None:
        pass
