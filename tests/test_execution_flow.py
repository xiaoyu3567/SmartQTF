import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from layers.execution.live import LiveExecutionEngine


class FlakyFakeOKXAdapter:
    def __init__(self):
        self.calls = []
        self.submitted_orders = []
        self.failures_left = 1

    def place_order(self, symbol, side, size, type, client_order_id=None, target_currency=None):
        call = {
            "symbol": symbol,
            "side": side,
            "size": size,
            "type": type,
            "client_order_id": client_order_id,
            "target_currency": target_currency,
        }
        self.calls.append(call)
        if self.failures_left:
            self.failures_left -= 1
            raise RuntimeError("temporary exchange error")
        self.submitted_orders.append(call)
        return {
            "success": True,
            "exchange": "okx",
            "code": "0",
            "data": [
                {
                    "ordId": "okx-order-1",
                    "clOrdId": client_order_id,
                    "sCode": "0",
                    "state": "partially_filled",
                    "accFillSz": "0.0004",
                }
            ],
        }


def test_execution():
    adapter = FlakyFakeOKXAdapter()
    engine = LiveExecutionEngine(adapter=adapter, max_retries=1, backoff_base=0)

    decision = {
        "symbol": "BTC-USDT",
        "action": "buy",
        "order_type": "market",
        "size": 0.001,
    }

    result = engine.execute(decision)
    duplicate = engine.execute(decision)
    print(result)

    assert len(adapter.submitted_orders) == 1
    assert len(adapter.calls) == 2
    assert adapter.calls[0]["size"] == 0.001
    assert adapter.calls[1]["size"] == 0.001
    assert adapter.calls[1]["target_currency"] == "base_ccy"
    assert result["retry_count"] == 1
    assert result["partial"] is True
    assert result["filled_size"] == 0.0004
    assert duplicate["idempotent_replay"] is True
    assert len(adapter.calls) == 2
