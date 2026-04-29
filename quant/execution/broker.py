from abc import ABC, abstractmethod
from typing import List

from quant.schemas.execution import (
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerProtectiveOrderRequest,
    BrokerProtectiveOrderResult,
    BrokerReplaceOrderRequest,
)


class BrokerAdapter(ABC):
    """Interface for simulated, paper, and live broker implementations."""

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def place_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, client_order_id: str) -> BrokerOrderResult:
        raise NotImplementedError

    def replace_order(self, request: BrokerReplaceOrderRequest) -> BrokerOrderResult:
        """Cancel/replace an open order without guessing broker state."""
        raise NotImplementedError

    def place_native_protective_order(
        self,
        request: BrokerProtectiveOrderRequest,
    ) -> BrokerProtectiveOrderResult:
        """Place a live-native protective stop/OCO order after the live gate approves."""
        raise NotImplementedError

    @abstractmethod
    def get_order(self, client_order_id: str) -> BrokerOrderResult:
        raise NotImplementedError

    @abstractmethod
    def list_open_orders(self, symbol: str | None = None) -> List[BrokerOrderResult]:
        raise NotImplementedError
