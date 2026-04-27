from quant.data.schemas.market import Kline
from quant.features.base.feature import Feature
from quant.features.time_guard import TimeGuard


class MovingAverage(Feature):
    def __init__(self, window: int):
        if window <= 0:
            raise ValueError("window must be greater than 0")
        self.window = window

    def compute(self, data, current_index):
        safe_data = TimeGuard.enforce(data, current_index)
        if len(safe_data) < self.window:
            return None

        window_data = safe_data[-self.window :]
        window_sum = sum(kline.close for kline in window_data)
        return window_sum / len(window_data)
