from quant.strategy.base.strategy import Strategy


class MACrossoverStrategy(Strategy):
    def __init__(self):
        self.signal_buffer = []
        self.generated_signal_indices = set()
        self.executed_signal_indices = set()

    def generate_signal(self, features, index):
        fast_ma = features["fast_ma"]
        slow_ma = features["slow_ma"]

        if index <= 0:
            return None

        previous_fast = fast_ma[index - 1]
        previous_slow = slow_ma[index - 1]
        current_fast = fast_ma[index]
        current_slow = slow_ma[index]

        if None in [previous_fast, previous_slow, current_fast, current_slow]:
            return None

        if previous_fast <= previous_slow and current_fast > current_slow:
            return {"signal": "buy", "signal_index": index}

        if previous_fast >= previous_slow and current_fast < current_slow:
            return {"signal": "sell", "signal_index": index}

        return None

    def on_bar(self, features, index):
        executions = self.execute_due_signals(index)
        signal = self.generate_signal(features, index)

        if signal is not None and signal["signal_index"] not in self.generated_signal_indices:
            signal["execute_index"] = index + 1
            self.signal_buffer.append(signal)
            self.generated_signal_indices.add(signal["signal_index"])

        return executions

    def execute_due_signals(self, index):
        executions = []
        next_buffer = []

        for signal in self.signal_buffer:
            signal_index = signal["signal_index"]

            if signal["execute_index"] == index and signal_index not in self.executed_signal_indices:
                executions.append(
                    {
                        "signal": signal["signal"],
                        "signal_index": signal_index,
                        "execute_index": index,
                    }
                )
                self.executed_signal_indices.add(signal_index)
            elif signal["execute_index"] > index:
                next_buffer.append(signal)

        self.signal_buffer = next_buffer
        return executions
