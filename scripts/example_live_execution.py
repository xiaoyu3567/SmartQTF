import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from layers.execution.live import LiveExecutionEngine
from quant.proxy import configure_process_proxy


def main():
    configure_process_proxy()
    engine = LiveExecutionEngine()
    decision = {
        "symbol": "BTC-USDT",
        "action": "buy",
        "order_type": "market",
        "size": 0.001,
    }
    result = engine.execute(decision)
    print(result)


if __name__ == "__main__":
    main()
