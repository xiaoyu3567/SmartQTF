from typing import List, Optional

from pydantic import BaseModel, Field

from quant.schemas.base import SmartQTFModel, TraceContext
from quant.schemas.enums import RegimeKind, TradeSide

if hasattr(BaseModel, "model_validate"):
    from pydantic import field_validator
else:
    from pydantic import validator


class StrategySignalBase(SmartQTFModel):
    signal_id: str
    strategy_id: str
    strategy_version: str
    side: TradeSide
    signal_index: int
    execute_index: Optional[int] = None
    symbol: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    reason_codes: List[str] = Field(default_factory=list)
    trace: Optional[TraceContext] = None

    @classmethod
    def indices_must_be_non_negative(cls, value):
        if value is not None and value < 0:
            raise ValueError("indices must be non-negative")
        return value

    def with_execute_index(self, execute_index: int):
        payload = self.to_payload()
        payload["execute_index"] = execute_index
        return self.__class__.from_payload(payload)

    def to_legacy_signal(self):
        payload = {
            "signal": self.side,
            "signal_index": self.signal_index,
        }
        if self.execute_index is not None:
            payload["execute_index"] = self.execute_index
        if self.symbol is not None:
            payload["symbol"] = self.symbol
        return payload


class StrategyRouteBase(SmartQTFModel):
    route_id: str
    timestamp: int
    symbol: str
    timeframe: str
    regime: RegimeKind
    strategy_id: str
    strategy_version: str
    router_id: str
    router_version: str
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    reason_codes: List[str] = Field(default_factory=list)
    trace: Optional[TraceContext] = None


if hasattr(BaseModel, "model_validate"):

    class StrategySignal(StrategySignalBase):
        @field_validator("signal_index", "execute_index")
        @classmethod
        def validate_indices(cls, value):
            return cls.indices_must_be_non_negative(value)


    class StrategyRoute(StrategyRouteBase):
        pass

else:

    class StrategySignal(StrategySignalBase):
        @validator("signal_index", "execute_index")
        def validate_indices(cls, value):
            return cls.indices_must_be_non_negative(value)


    class StrategyRoute(StrategyRouteBase):
        pass
