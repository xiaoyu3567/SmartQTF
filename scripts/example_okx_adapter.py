import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.exchange.okx import OKXAdapter
from quant.proxy import configure_process_proxy


def main():
    configure_process_proxy()
    exchange = OKXAdapter()
    balance = exchange.get_balance()
    print(balance)


if __name__ == "__main__":
    main()
