import inspect
from typing import Any, Callable, Dict, List, Optional

from quant.data.schemas.market import Kline, KlineBatch
from quant.schemas.base import SmartQTFModel


class TimeframeKlineRequest(SmartQTFModel):
    symbol: str
    timeframe: str
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    limit: int = 100

    def __init__(self, **data):
        super().__init__(**data)
        symbol = self.symbol.strip()
        timeframe = self.timeframe.strip()
        if not symbol:
            raise ValueError("symbol must not be empty")
        if not timeframe:
            raise ValueError("timeframe must not be empty")
        if self.limit <= 0:
            raise ValueError("limit must be positive")
        if self.start_ts is not None and self.start_ts < 0:
            raise ValueError("start_ts must be non-negative")
        if self.end_ts is not None and self.end_ts < 0:
            raise ValueError("end_ts must be non-negative")
        if self.start_ts is not None and self.end_ts is not None and self.end_ts < self.start_ts:
            raise ValueError("end_ts must be greater than or equal to start_ts")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "timeframe", timeframe)


class MultiTimeframeDataRequest(SmartQTFModel):
    symbol: str
    execution_timeframe: str
    context_timeframes: List[str]
    venue: str = "unknown"
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    limit: int = 100

    def __init__(self, **data):
        super().__init__(**data)
        symbol = self.symbol.strip()
        venue = self.venue.strip() or "unknown"
        execution_timeframe = self.execution_timeframe.strip()
        context_timeframes = [timeframe.strip() for timeframe in self.context_timeframes]
        all_timeframes = [execution_timeframe] + context_timeframes

        if not symbol:
            raise ValueError("symbol must not be empty")
        if not execution_timeframe:
            raise ValueError("execution_timeframe must not be empty")
        if any(not timeframe for timeframe in context_timeframes):
            raise ValueError("context_timeframes must not contain empty values")
        if len(all_timeframes) != len(set(all_timeframes)):
            raise ValueError("execution and context timeframes must be unique")
        if self.limit <= 0:
            raise ValueError("limit must be positive")
        if self.start_ts is not None and self.start_ts < 0:
            raise ValueError("start_ts must be non-negative")
        if self.end_ts is not None and self.end_ts < 0:
            raise ValueError("end_ts must be non-negative")
        if self.start_ts is not None and self.end_ts is not None and self.end_ts < self.start_ts:
            raise ValueError("end_ts must be greater than or equal to start_ts")

        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "venue", venue)
        object.__setattr__(self, "execution_timeframe", execution_timeframe)
        object.__setattr__(self, "context_timeframes", context_timeframes)

    @property
    def timeframes(self) -> List[str]:
        return [self.execution_timeframe] + list(self.context_timeframes)

    def to_timeframe_requests(self) -> List[TimeframeKlineRequest]:
        return [
            TimeframeKlineRequest(
                symbol=self.symbol,
                timeframe=timeframe,
                start_ts=self.start_ts,
                end_ts=self.end_ts,
                limit=self.limit,
            )
            for timeframe in self.timeframes
        ]


class TimeframeKlineBatch(SmartQTFModel):
    symbol: str
    timeframe: str
    venue: str = "unknown"
    role: str = "context"
    klines: List[Kline]
    source_request: Optional[TimeframeKlineRequest] = None

    def __init__(self, **data):
        super().__init__(**data)
        symbol = self.symbol.strip()
        timeframe = self.timeframe.strip()
        venue = self.venue.strip() or "unknown"
        role = self.role.strip().lower()

        if not symbol:
            raise ValueError("symbol must not be empty")
        if not timeframe:
            raise ValueError("timeframe must not be empty")
        if role not in {"execution", "context"}:
            raise ValueError("role must be execution or context")

        timestamps = [kline.timestamp for kline in self.klines]
        if timestamps != sorted(timestamps):
            raise ValueError("klines must be sorted by timestamp")
        if len(timestamps) != len(set(timestamps)):
            raise ValueError("klines must not contain duplicate timestamps")

        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "timeframe", timeframe)
        object.__setattr__(self, "venue", venue)
        object.__setattr__(self, "role", role)

    @property
    def first_timestamp(self) -> Optional[int]:
        if not self.klines:
            return None
        return self.klines[0].timestamp

    @property
    def last_timestamp(self) -> Optional[int]:
        if not self.klines:
            return None
        return self.klines[-1].timestamp

    @property
    def checked_count(self) -> int:
        return len(self.klines)

    @classmethod
    def from_kline_batch(
        cls,
        batch: KlineBatch,
        *,
        role: str,
        source_request: Optional[TimeframeKlineRequest] = None,
    ) -> "TimeframeKlineBatch":
        return cls(
            symbol=batch.symbol,
            timeframe=batch.timeframe,
            venue=batch.venue,
            role=role,
            klines=batch.klines,
            source_request=source_request,
        )

    def to_kline_batch(self) -> KlineBatch:
        return KlineBatch(
            symbol=self.symbol,
            timeframe=self.timeframe,
            venue=self.venue,
            klines=self.klines,
        )


class MultiTimeframeKlineBatch(SmartQTFModel):
    symbol: str
    execution_timeframe: str
    execution: Optional[TimeframeKlineBatch]
    contexts: List[TimeframeKlineBatch]
    venue: str = "unknown"
    as_of_timestamp: Optional[int] = None

    def __init__(self, **data):
        super().__init__(**data)
        symbol = self.symbol.strip()
        execution_timeframe = self.execution_timeframe.strip()
        venue = self.venue.strip() or "unknown"

        if not symbol:
            raise ValueError("symbol must not be empty")
        if not execution_timeframe:
            raise ValueError("execution_timeframe must not be empty")
        if self.as_of_timestamp is not None and self.as_of_timestamp < 0:
            raise ValueError("as_of_timestamp must be non-negative")

        if self.execution is not None:
            if self.execution.symbol != symbol:
                raise ValueError("execution batch symbol must match multi-timeframe symbol")
            if self.execution.timeframe != execution_timeframe:
                raise ValueError("execution batch timeframe must match execution_timeframe")
            if self.execution.role != "execution":
                raise ValueError("execution batch role must be execution")

        context_timeframes = []
        for context in self.contexts:
            if context.symbol != symbol:
                raise ValueError("context batch symbol must match multi-timeframe symbol")
            if context.role != "context":
                raise ValueError("context batch role must be context")
            if context.timeframe == execution_timeframe:
                raise ValueError("context timeframe must not duplicate execution timeframe")
            context_timeframes.append(context.timeframe)

        if len(context_timeframes) != len(set(context_timeframes)):
            raise ValueError("context timeframes must be unique")

        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "execution_timeframe", execution_timeframe)
        object.__setattr__(self, "venue", venue)

    @property
    def context_timeframes(self) -> List[str]:
        return [context.timeframe for context in self.contexts]

    @property
    def timeframe_batches(self) -> Dict[str, TimeframeKlineBatch]:
        batches: Dict[str, TimeframeKlineBatch] = {}
        if self.execution is not None:
            batches[self.execution.timeframe] = self.execution
        for context in self.contexts:
            batches[context.timeframe] = context
        return batches


class MultiTimeframeKlineProvider:
    """Adapter around a single-timeframe provider that builds a typed envelope."""

    def __init__(self, provider: Any, venue: str = "unknown"):
        self.provider = provider
        self.venue = venue

    def get_multi_timeframe_klines(
        self,
        request: MultiTimeframeDataRequest,
    ) -> MultiTimeframeKlineBatch:
        return build_multi_timeframe_kline_batch(
            provider=self.provider,
            request=request,
            venue=self.venue,
        )


def build_multi_timeframe_kline_batch(
    provider: Any,
    request: MultiTimeframeDataRequest,
    venue: Optional[str] = None,
) -> MultiTimeframeKlineBatch:
    batches = []
    for timeframe_request in request.to_timeframe_requests():
        role = "execution" if timeframe_request.timeframe == request.execution_timeframe else "context"
        batches.append(_fetch_timeframe_batch(provider, timeframe_request, venue or request.venue, role))

    execution = batches[0]
    contexts = batches[1:]
    return MultiTimeframeKlineBatch(
        symbol=request.symbol,
        venue=venue or request.venue,
        execution_timeframe=request.execution_timeframe,
        execution=execution,
        contexts=contexts,
        as_of_timestamp=execution.last_timestamp,
    )


def _fetch_timeframe_batch(
    provider: Any,
    request: TimeframeKlineRequest,
    venue: str,
    role: str,
) -> TimeframeKlineBatch:
    get_batch = getattr(provider, "get_kline_batch", None)
    if callable(get_batch):
        result = _call_provider_method(get_batch, request)
    else:
        get_klines = getattr(provider, "get_klines", None)
        if not callable(get_klines):
            raise TypeError("provider must expose get_kline_batch() or get_klines()")
        result = _call_provider_method(get_klines, request)

    if isinstance(result, KlineBatch):
        return TimeframeKlineBatch.from_kline_batch(result, role=role, source_request=request)

    klines = list(result)
    return TimeframeKlineBatch(
        symbol=request.symbol,
        timeframe=request.timeframe,
        venue=venue,
        role=role,
        klines=klines,
        source_request=request,
    )


def _call_provider_method(method: Callable[..., Any], request: TimeframeKlineRequest) -> Any:
    kwargs = {
        "symbol": request.symbol,
        "timeframe": request.timeframe,
        "start_ts": request.start_ts,
        "end_ts": request.end_ts,
        "limit": request.limit,
    }
    return method(**_filter_supported_kwargs(method, kwargs))


def _filter_supported_kwargs(method: Callable[..., Any], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    signature = inspect.signature(method)
    parameters = signature.parameters
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return {key: value for key, value in kwargs.items() if value is not None}

    supported = {}
    for key, value in kwargs.items():
        if value is None:
            continue
        if key in parameters:
            supported[key] = value
    return supported
