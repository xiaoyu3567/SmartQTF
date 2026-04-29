from quant.features.base.feature import Feature
from quant.features.time_guard import TimeGuard


def _validate_window(window: int) -> None:
    if window <= 0:
        raise ValueError("window must be greater than 0")


class ExponentialMovingAverage(Feature):
    def __init__(self, window: int):
        _validate_window(window)
        self.window = window

    def compute(self, data, current_index):
        safe_data = TimeGuard.enforce(data, current_index)
        if len(safe_data) < self.window:
            return None

        closes = [kline.close for kline in safe_data]
        alpha = 2 / (self.window + 1)
        ema = sum(closes[: self.window]) / self.window
        for close in closes[self.window :]:
            ema = (close * alpha) + (ema * (1 - alpha))
        return ema


class RelativeStrengthIndex(Feature):
    def __init__(self, window: int = 14):
        _validate_window(window)
        self.window = window

    def compute(self, data, current_index):
        safe_data = TimeGuard.enforce(data, current_index)
        if len(safe_data) < self.window + 1:
            return None

        closes = [kline.close for kline in safe_data]
        changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
        window_changes = changes[-self.window :]
        gains = [max(change, 0.0) for change in window_changes]
        losses = [abs(min(change, 0.0)) for change in window_changes]
        average_gain = sum(gains) / self.window
        average_loss = sum(losses) / self.window

        if average_loss == 0:
            return 100.0

        relative_strength = average_gain / average_loss
        return 100 - (100 / (1 + relative_strength))


class MovingAverageConvergenceDivergence(Feature):
    def __init__(self, fast_window: int = 12, slow_window: int = 26, signal_window: int = 9):
        _validate_window(fast_window)
        _validate_window(slow_window)
        _validate_window(signal_window)
        if fast_window >= slow_window:
            raise ValueError("fast_window must be less than slow_window")

        self.fast_window = fast_window
        self.slow_window = slow_window
        self.signal_window = signal_window

    def compute(self, data, current_index):
        safe_data = TimeGuard.enforce(data, current_index)
        if len(safe_data) < self.slow_window:
            return None

        macd_values = []
        for index in range(len(safe_data)):
            fast = ExponentialMovingAverage(self.fast_window).compute(safe_data, index)
            slow = ExponentialMovingAverage(self.slow_window).compute(safe_data, index)
            if fast is not None and slow is not None:
                macd_values.append(fast - slow)

        if not macd_values:
            return None

        macd = macd_values[-1]
        signal = None
        histogram = None
        if len(macd_values) >= self.signal_window:
            alpha = 2 / (self.signal_window + 1)
            signal = sum(macd_values[: self.signal_window]) / self.signal_window
            for value in macd_values[self.signal_window :]:
                signal = (value * alpha) + (signal * (1 - alpha))
            histogram = macd - signal

        return {
            "macd": macd,
            "signal": signal,
            "histogram": histogram,
        }


class AverageTrueRange(Feature):
    def __init__(self, window: int = 14):
        _validate_window(window)
        self.window = window

    def compute(self, data, current_index):
        safe_data = TimeGuard.enforce(data, current_index)
        if len(safe_data) < self.window + 1:
            return None

        true_ranges = []
        for index in range(1, len(safe_data)):
            current = safe_data[index]
            previous = safe_data[index - 1]
            true_ranges.append(
                max(
                    current.high - current.low,
                    abs(current.high - previous.close),
                    abs(current.low - previous.close),
                )
            )

        return sum(true_ranges[-self.window :]) / self.window
