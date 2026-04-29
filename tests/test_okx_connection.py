import logging
import os
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.exchange.okx import OKXAdapter


TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in TRUE_VALUES


def test_connection(caplog):
    if not _env_enabled("SMARTQTF_RUN_OKX_CONNECTION_TEST"):
        pytest.skip("set SMARTQTF_RUN_OKX_CONNECTION_TEST=1 to run the live OKX connection test")

    missing = [
        name
        for name in ("OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE")
        if not os.getenv(name)
    ]
    if missing:
        pytest.skip(f"missing OKX credentials: {', '.join(missing)}")

    caplog.set_level(logging.INFO, logger="adapters.exchange.okx")
    ex = OKXAdapter()
    balance = ex.get_balance()
    print(balance)

    assert balance["success"] is True
    assert balance["exchange"] == "okx"
    assert "data" in balance

    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "okx_request" in logs
    assert os.environ["OKX_API_KEY"] not in logs
    assert os.environ["OKX_SECRET"] not in logs
    assert os.environ["OKX_PASSPHRASE"] not in logs
