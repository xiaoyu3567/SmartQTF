from typing import Any, Dict, Optional, Type, TypeVar

from pydantic import BaseModel

if hasattr(BaseModel, "model_validate"):
    from pydantic import ConfigDict
else:
    ConfigDict = None

from quant.schemas.enums import LayerName, PayloadSource


ModelT = TypeVar("ModelT", bound="SmartQTFModel")


class SmartQTFModel(BaseModel):
    if ConfigDict is not None:
        model_config = ConfigDict(use_enum_values=True)

    schema_version: str = "1.0"

    def to_payload(self) -> Dict[str, Any]:
        if hasattr(self, "model_dump"):
            return self.model_dump(mode="json")
        return self.dict()

    @classmethod
    def from_payload(cls: Type[ModelT], payload: Dict[str, Any]) -> ModelT:
        return cls(**payload)

    if ConfigDict is None:
        class Config:
            use_enum_values = True


class TraceContext(SmartQTFModel):
    run_id: str
    source: PayloadSource = PayloadSource.BACKTEST
    symbol: str
    timeframe: Optional[str] = None
    timestamp: Optional[int] = None
    bar_index: Optional[int] = None


class LayerRejection(SmartQTFModel):
    layer: LayerName
    code: str
    message: str
    fatal: bool = False
