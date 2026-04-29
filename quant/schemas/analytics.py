from typing import List

from pydantic import BaseModel, Field

from quant.schemas.base import SmartQTFModel

if hasattr(BaseModel, "model_validate"):
    from pydantic import field_validator
else:
    from pydantic import validator


class AttributionBucketBase(SmartQTFModel):
    bucket_type: str
    bucket_value: str
    gross_pnl: float = 0.0
    fees: float = 0.0
    net_pnl: float = 0.0
    fill_count: int = 0
    trade_count: int = 0
    winning_trades: int = 0
    losing_trades: int = 0

    @property
    def win_rate(self) -> float:
        if self.trade_count == 0:
            return 0.0
        return self.winning_trades / self.trade_count

    @classmethod
    def non_negative_count(cls, value):
        if value < 0:
            raise ValueError("counts must be non-negative")
        return value


class TradeAttributionReportBase(SmartQTFModel):
    report_id: str
    run_id: str
    generated_at: int
    buckets: List[AttributionBucketBase] = Field(default_factory=list)
    total_gross_pnl: float = 0.0
    total_fees: float = 0.0
    total_net_pnl: float = 0.0
    fill_count: int = 0
    trade_count: int = 0

    def buckets_for(self, bucket_type: str) -> List[AttributionBucketBase]:
        return [bucket for bucket in self.buckets if bucket.bucket_type == bucket_type]


class DailyReviewBucketBase(SmartQTFModel):
    bucket_type: str
    bucket_value: str
    gross_pnl: float = 0.0
    fees: float = 0.0
    net_pnl: float = 0.0
    average_net_pnl: float = 0.0
    win_rate: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    fill_count: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    rejection_count: int = 0
    anomaly_count: int = 0

    @classmethod
    def non_negative_count(cls, value):
        if value < 0:
            raise ValueError("counts must be non-negative")
        return value


class DailyReviewReportBase(SmartQTFModel):
    report_id: str
    run_id: str
    trading_date: str
    generated_at: int
    buckets: List[DailyReviewBucketBase] = Field(default_factory=list)
    total_gross_pnl: float = 0.0
    total_fees: float = 0.0
    total_net_pnl: float = 0.0
    fill_count: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    rejection_count: int = 0
    anomaly_count: int = 0
    summary_text: str = ""

    def buckets_for(self, bucket_type: str) -> List[DailyReviewBucketBase]:
        return [bucket for bucket in self.buckets if bucket.bucket_type == bucket_type]


if hasattr(BaseModel, "model_validate"):

    class AttributionBucket(AttributionBucketBase):
        @field_validator("fill_count", "trade_count", "winning_trades", "losing_trades")
        @classmethod
        def validate_counts(cls, value):
            return cls.non_negative_count(value)

    class TradeAttributionReport(TradeAttributionReportBase):
        buckets: List[AttributionBucket] = Field(default_factory=list)

        @field_validator("fill_count", "trade_count")
        @classmethod
        def validate_counts(cls, value):
            return AttributionBucket.non_negative_count(value)

    class DailyReviewBucket(DailyReviewBucketBase):
        @field_validator(
            "fill_count",
            "winning_trades",
            "losing_trades",
            "rejection_count",
            "anomaly_count",
        )
        @classmethod
        def validate_counts(cls, value):
            return cls.non_negative_count(value)

    class DailyReviewReport(DailyReviewReportBase):
        buckets: List[DailyReviewBucket] = Field(default_factory=list)

        @field_validator(
            "fill_count",
            "winning_trades",
            "losing_trades",
            "rejection_count",
            "anomaly_count",
        )
        @classmethod
        def validate_counts(cls, value):
            return DailyReviewBucket.non_negative_count(value)

else:

    class AttributionBucket(AttributionBucketBase):
        @validator("fill_count", "trade_count", "winning_trades", "losing_trades")
        def validate_counts(cls, value):
            return cls.non_negative_count(value)

    class TradeAttributionReport(TradeAttributionReportBase):
        buckets: List[AttributionBucket] = Field(default_factory=list)

        @validator("fill_count", "trade_count")
        def validate_counts(cls, value):
            return AttributionBucket.non_negative_count(value)

    class DailyReviewBucket(DailyReviewBucketBase):
        @validator(
            "fill_count",
            "winning_trades",
            "losing_trades",
            "rejection_count",
            "anomaly_count",
        )
        def validate_counts(cls, value):
            return cls.non_negative_count(value)

    class DailyReviewReport(DailyReviewReportBase):
        buckets: List[DailyReviewBucket] = Field(default_factory=list)

        @validator(
            "fill_count",
            "winning_trades",
            "losing_trades",
            "rejection_count",
            "anomaly_count",
        )
        def validate_counts(cls, value):
            return DailyReviewBucket.non_negative_count(value)
