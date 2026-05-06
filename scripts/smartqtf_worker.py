import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.orchestration.worker_runtime import SmartQTFWorkerRuntime

try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel
except ImportError:
    FastAPI = None
    HTTPException = None
    BaseModel = object


DEFAULT_CONFIG_PATH = os.environ.get("SMARTQTF_WORKER_CONFIG")
runtime = SmartQTFWorkerRuntime(config_path=DEFAULT_CONFIG_PATH)


class StartRequest(BaseModel):
    config_path: str | None = None
    index: int | None = None
    poll_interval_seconds: float | None = None


class RunOnceRequest(BaseModel):
    config_path: str | None = None
    requested_at: int | None = None
    index: int | None = None
    batch_id: str | None = None


class PromotionReviewRequest(BaseModel):
    action: str
    artifact_id: str
    reviewer_note: str = ""
    reviewer: str | None = None
    dry_run: bool = True
    manual_review: bool = False


def create_app(worker_runtime=None):
    if FastAPI is None:
        raise RuntimeError("FastAPI is required for scripts/smartqtf_worker.py API mode")

    worker = worker_runtime or runtime
    app = FastAPI(title="SmartQTF Worker", version="1.0")

    @app.get("/health")
    def health():
        return {"ok": True, "service": "smartqtf-worker", "status": worker.status()}

    @app.get("/status")
    def status():
        return worker.status()

    @app.post("/start")
    def start(request: StartRequest):
        return _call(worker.start, config_path=request.config_path, index=request.index, poll_interval_seconds=request.poll_interval_seconds)

    @app.post("/stop")
    def stop():
        return _call(worker.stop)

    @app.post("/run-once")
    def run_once(request: RunOnceRequest):
        return _call(
            worker.run_once,
            config_path=request.config_path,
            requested_at=request.requested_at,
            index=request.index,
            batch_id=request.batch_id,
        )

    @app.get("/testflow")
    def testflow():
        return worker.testflow()

    @app.get("/kline")
    def kline(symbol: str | None = None, timeframe: str | None = None):
        return worker.kline(symbol=symbol, timeframe=timeframe)

    @app.get("/logs")
    def logs(
        limit: int = 100,
        run_id: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        event_type: str | None = None,
    ):
        return worker.logs(
            limit=limit,
            run_id=run_id,
            symbol=symbol,
            timeframe=timeframe,
            event_type=event_type,
        )

    @app.get("/optimization")
    def optimization():
        return worker.optimization()

    @app.post("/optimization/review")
    def promotion_review(request: PromotionReviewRequest):
        return _call(
            worker.record_promotion_review,
            action=request.action,
            artifact_id=request.artifact_id,
            reviewer_note=request.reviewer_note,
            reviewer=request.reviewer,
            dry_run=request.dry_run,
            manual_review=request.manual_review,
        )

    return app


def _call(fn, **kwargs):
    kwargs = {key: value for key, value in kwargs.items() if value is not None}
    try:
        return fn(**kwargs)
    except Exception as exc:
        if HTTPException is None:
            raise
        raise HTTPException(status_code=400, detail=str(exc)) from exc


if FastAPI is not None:
    app = create_app(runtime)
else:
    app = None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the SmartQTF worker API.")
    parser.add_argument("--host", default=os.environ.get("SMARTQTF_WORKER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SMARTQTF_WORKER_PORT", "6667")))
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args(argv)

    if args.config:
        runtime.config_path = args.config

    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("uvicorn is required to serve the SmartQTF worker API") from exc
    if app is None:
        raise SystemExit("FastAPI is required to serve the SmartQTF worker API")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
