from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from quant.schemas.base import SmartQTFModel, TraceContext

if hasattr(BaseModel, "model_validate"):
    from pydantic import field_validator
else:
    from pydantic import validator


class UniverseInstrumentBase(SmartQTFModel):
    symbol: str
    venue: str
    instrument_type: str
    base_currency: str
    quote_currency: str
    status: str
    quantity_step: float = 0.0
    min_quantity: float = 0.0
    max_quantity: Optional[float] = None
    price_tick: Optional[float] = None
    min_notional: float = 0.0
    volume_24h: Optional[float] = None
    turnover_24h: Optional[float] = None
    last_price: Optional[float] = None
    metadata: Dict[str, str] = Field(default_factory=dict)
    trace: Optional[TraceContext] = None

    @classmethod
    def text_must_not_be_empty(cls, value, field_name):
        if not str(value).strip():
            raise ValueError(f"{field_name} must not be empty")
        return str(value).strip()

    @classmethod
    def number_must_be_non_negative(cls, value, field_name):
        if value < 0.0:
            raise ValueError(f"{field_name} must be non-negative")
        return value

    @classmethod
    def optional_number_must_be_non_negative(cls, value, field_name):
        if value is not None and value < 0.0:
            raise ValueError(f"{field_name} must be non-negative")
        return value


class UniverseFilterConfigBase(SmartQTFModel):
    venue: str = "okx"
    instrument_type: str = "SPOT"
    quote_currencies: List[str] = Field(default_factory=lambda: ["USDT"])
    allowed_statuses: List[str] = Field(default_factory=lambda: ["live"])
    blacklist: List[str] = Field(default_factory=list)
    min_volume_24h: float = 0.0
    min_turnover_24h: float = 0.0
    max_min_quantity: Optional[float] = None
    max_min_notional: Optional[float] = None
    require_order_rules: bool = True

    @classmethod
    def text_must_not_be_empty(cls, value, field_name):
        if not str(value).strip():
            raise ValueError(f"{field_name} must not be empty")
        return str(value).strip()

    @classmethod
    def number_must_be_non_negative(cls, value, field_name):
        if value < 0.0:
            raise ValueError(f"{field_name} must be non-negative")
        return value

    @classmethod
    def optional_number_must_be_non_negative(cls, value, field_name):
        if value is not None and value < 0.0:
            raise ValueError(f"{field_name} must be non-negative")
        return value


class UniverseRejection(SmartQTFModel):
    symbol: str
    reason_code: str
    reason: str


if hasattr(BaseModel, "model_validate"):

    class UniverseInstrument(UniverseInstrumentBase):
        @field_validator("symbol", "venue", "instrument_type", "base_currency", "quote_currency", "status")
        @classmethod
        def validate_text(cls, value, info):
            return cls.text_must_not_be_empty(value, info.field_name)

        @field_validator("quantity_step", "min_quantity", "min_notional")
        @classmethod
        def validate_non_negative(cls, value, info):
            return cls.number_must_be_non_negative(value, info.field_name)

        @field_validator("max_quantity", "price_tick", "volume_24h", "turnover_24h", "last_price")
        @classmethod
        def validate_optional_non_negative(cls, value, info):
            return cls.optional_number_must_be_non_negative(value, info.field_name)

    class UniverseFilterConfig(UniverseFilterConfigBase):
        @field_validator("venue", "instrument_type")
        @classmethod
        def validate_text(cls, value, info):
            return cls.text_must_not_be_empty(value, info.field_name)

        @field_validator("min_volume_24h", "min_turnover_24h")
        @classmethod
        def validate_non_negative(cls, value, info):
            return cls.number_must_be_non_negative(value, info.field_name)

        @field_validator("max_min_quantity", "max_min_notional")
        @classmethod
        def validate_optional_non_negative(cls, value, info):
            return cls.optional_number_must_be_non_negative(value, info.field_name)

else:

    class UniverseInstrument(UniverseInstrumentBase):
        @validator("symbol", "venue", "instrument_type", "base_currency", "quote_currency", "status")
        def validate_text(cls, value, field):
            return cls.text_must_not_be_empty(value, field.name)

        @validator("quantity_step", "min_quantity", "min_notional")
        def validate_non_negative(cls, value, field):
            return cls.number_must_be_non_negative(value, field.name)

        @validator("max_quantity", "price_tick", "volume_24h", "turnover_24h", "last_price")
        def validate_optional_non_negative(cls, value, field):
            return cls.optional_number_must_be_non_negative(value, field.name)

    class UniverseFilterConfig(UniverseFilterConfigBase):
        @validator("venue", "instrument_type")
        def validate_text(cls, value, field):
            return cls.text_must_not_be_empty(value, field.name)

        @validator("min_volume_24h", "min_turnover_24h")
        def validate_non_negative(cls, value, field):
            return cls.number_must_be_non_negative(value, field.name)

        @validator("max_min_quantity", "max_min_notional")
        def validate_optional_non_negative(cls, value, field):
            return cls.optional_number_must_be_non_negative(value, field.name)


class UniverseSnapshot(SmartQTFModel):
    snapshot_id: str
    venue: str
    instrument_type: str
    as_of_timestamp: int
    source: str
    filters: UniverseFilterConfig
    instruments: List[UniverseInstrument]
    rejected: List[UniverseRejection] = Field(default_factory=list)
    trace: Optional[TraceContext] = None
