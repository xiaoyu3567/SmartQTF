"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { callSmartQTF } from "@/lib/smartqtf-client";
import type {
  KlinePayload,
  LogsPayload,
  OptimizationPayload,
  PromotionReviewCandidate,
  RuntimeStatus,
  RuntimeTab,
  TestflowPayload,
  SmartQTFApiResult,
  SmartQTFJson
} from "@/lib/smartqtf-types";

const TABS: Array<{ id: RuntimeTab; label: string }> = [
  { id: "main", label: "Main" },
  { id: "testflow", label: "TestFlow" },
  { id: "logs", label: "Logs" },
  { id: "optimization", label: "Optimization" }
];

const CONTEXT_TIMEFRAMES = ["15m", "1h", "4h"];

type ConsoleState = {
  status?: SmartQTFApiResult<RuntimeStatus>;
  kline?: SmartQTFApiResult<KlinePayload>;
  testflow?: SmartQTFApiResult<TestflowPayload>;
  logs?: SmartQTFApiResult<LogsPayload>;
  optimization?: SmartQTFApiResult<OptimizationPayload>;
  lastAction?: SmartQTFApiResult<SmartQTFJson>;
};

export function RuntimeConsole() {
  const [activeTab, setActiveTab] = useState<RuntimeTab>("main");
  const [state, setState] = useState<ConsoleState>({});
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [logFilters, setLogFilters] = useState({ runId: "", symbol: "", timeframe: "" });

  const refresh = useCallback(async () => {
    setBusyAction("refresh");
    try {
      const logQuery = new URLSearchParams({ limit: "20" });
      if (logFilters.runId.trim()) {
        logQuery.set("run_id", logFilters.runId.trim());
      }
      if (logFilters.symbol.trim()) {
        logQuery.set("symbol", logFilters.symbol.trim());
      }
      if (logFilters.timeframe.trim()) {
        logQuery.set("timeframe", logFilters.timeframe.trim());
      }
      const [status, kline, testflow, logs, optimization] = await Promise.all([
        callSmartQTF<RuntimeStatus>("/api/smartqtf/status"),
        callSmartQTF<KlinePayload>("/api/smartqtf/kline?symbol=BTCUSDT&timeframe=5m"),
        callSmartQTF<TestflowPayload>("/api/smartqtf/testflow"),
        callSmartQTF<LogsPayload>(`/api/smartqtf/logs?${logQuery.toString()}`),
        callSmartQTF<OptimizationPayload>("/api/smartqtf/optimization")
      ]);
      setState((current) => ({ ...current, status, kline, testflow, logs, optimization }));
      setLastUpdated(new Date().toLocaleTimeString());
    } finally {
      setBusyAction(null);
    }
  }, [logFilters.runId, logFilters.symbol, logFilters.timeframe]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const runAction = async (label: string, path: string, body: SmartQTFJson = {}) => {
    setBusyAction(label);
    try {
      const result = await callSmartQTF(path, { method: "POST", body });
      setState((current) => ({ ...current, lastAction: result }));
      await refresh();
    } finally {
      setBusyAction(null);
    }
  };

  const workerRunning = Boolean(state.status?.data?.running);
  const executionTimeframe = state.kline?.data?.execution_timeframe ?? "5m";
  const contextTimeframes = useMemo(() => {
    const fromPayload = state.kline?.data?.context_timeframes;
    return fromPayload && fromPayload.length > 0 ? fromPayload : CONTEXT_TIMEFRAMES;
  }, [state.kline?.data?.context_timeframes]);
  const health = state.status?.data?.scan_loop_health;
  const latestPointer = state.status?.data?.latest_report_pointer;
  const safeMode = state.status?.data?.safety?.safe_mode;

  return (
    <main className="console-shell">
      <section className="top-bar">
        <div>
          <p className="eyebrow">SmartQTF</p>
          <h1>Runtime Console</h1>
        </div>
        <div className="status-cluster" aria-label="Worker status">
          <span className={workerRunning ? "status-dot running" : "status-dot"} />
          <span>{workerRunning ? "Running" : "Stopped"}</span>
          {lastUpdated ? <span className="muted">Updated {lastUpdated}</span> : null}
        </div>
      </section>

      <section className="control-strip">
        <button
          type="button"
          onClick={() => runAction("start", "/api/smartqtf/start", { index: 0 })}
          disabled={busyAction !== null}
        >
          Start Scan Loop
        </button>
        <button type="button" onClick={() => runAction("stop", "/api/smartqtf/stop")} disabled={busyAction !== null}>
          Stop
        </button>
        <button
          type="button"
          onClick={() =>
            runAction("run-once", "/api/smartqtf/run-once", {
              requested_at: Math.floor(Date.now() / 1000),
              index: 0,
              batch_id: `web-${Date.now()}`
            })
          }
          disabled={busyAction !== null}
        >
          Run Once
        </button>
        <button type="button" onClick={refresh} disabled={busyAction !== null}>
          Refresh
        </button>
      </section>

      <section className="runtime-grid" aria-label="Runtime overview">
        <Metric label="Scan Health" value={health?.state ?? (workerRunning ? "running" : "stopped")} />
        <Metric label="Last Run" value={formatAge(health?.last_run_age_seconds)} />
        <Metric label="Latest Report" value={latestPointer?.run_id ?? "none"} />
        <Metric label="Safe Mode" value={safeMode === false ? "false" : "true"} />
      </section>

      <section className="tab-list" aria-label="Console tabs">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={activeTab === tab.id ? "active" : ""}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </section>

      <section className="panel">
        {activeTab === "main" ? (
          <MainPanel status={state.status} kline={state.kline} lastAction={state.lastAction} busyAction={busyAction} />
        ) : null}
        {activeTab === "testflow" ? <TestFlowPanel result={state.testflow} /> : null}
        {activeTab === "logs" ? (
          <LogsPanel result={state.logs} filters={logFilters} onFiltersChange={setLogFilters} onRefresh={refresh} />
        ) : null}
        {activeTab === "optimization" ? <OptimizationPanel result={state.optimization} /> : null}
      </section>
    </main>
  );
}

function MainPanel({
  status,
  kline,
  lastAction,
  busyAction
}: {
  status?: SmartQTFApiResult<RuntimeStatus>;
  kline?: SmartQTFApiResult<KlinePayload>;
  lastAction?: SmartQTFApiResult<SmartQTFJson>;
  busyAction: string | null;
}) {
  const data = status?.data;
  const health = data?.scan_loop_health ?? {};
  const latestPointer = data?.latest_report_pointer;
  const latestReplay = data?.latest_run_replay;
  const klineSnapshot = kline?.data?.worker_cache;
  const batches = klineSnapshot?.batches ?? {};
  const timeframeEntries = [
    kline?.data?.execution_timeframe ?? "5m",
    ...(kline?.data?.context_timeframes ?? CONTEXT_TIMEFRAMES)
  ].map((timeframe) => [timeframe, batches[timeframe]] as const);

  return (
    <div className="main-panel">
      <div className="summary-row">
        <article className="status-box">
          <span>Scan Loop</span>
          <strong className={!data?.last_error ? "healthy" : ""}>{health.state ?? "unknown"}</strong>
          <p>{data?.last_error ?? `Last run ${formatAge(health.last_run_age_seconds)}`}</p>
        </article>
        <article className="status-box">
          <span>Latest Report</span>
          <strong className={latestPointer?.success === false ? "" : "healthy"}>{latestPointer?.symbol ?? "none"}</strong>
          <p>{latestPointer?.run_id ?? "run_once_required"}</p>
        </article>
        <article className="status-box">
          <span>Safety</span>
          <strong className={data?.safety?.safe_mode === false ? "" : "healthy"}>
            {data?.safety?.safe_mode === false ? "live capable" : "safe mode"}
          </strong>
          <p>{`dry_run=${yesNo(data?.safety?.dry_run)} live_orders=${yesNo(data?.safety?.live_order_submission)}`}</p>
        </article>
      </div>
      <div className="summary-row">
        <StatusBox title="Worker API" result={status} />
        <StatusBox title="Kline Channel" result={kline} />
        <StatusBox title="Last Action" result={lastAction} fallback={busyAction ? `Running ${busyAction}` : "No action yet"} />
      </div>
      <div className="timeframe-row">
        <span>BTCUSDT</span>
        <span>5m execution</span>
        <span>15m context</span>
        <span>1h context</span>
        <span>4h context</span>
      </div>
      <div className="detail-grid" aria-label="Multi-timeframe freshness">
        {timeframeEntries.map(([timeframe, channel]) => (
          <DetailBox
            key={timeframe}
            title={`${timeframe} ${channel?.role ?? "context"}`}
            status={channel?.freshness?.stale ? "Stale" : channel?.available === false ? "Missing" : "Fresh"}
            rows={[
              ["Bars", String(channel?.bar_count ?? 0)],
              ["Freshness", channel?.freshness?.reason ?? kline?.data?.reason ?? "pending"],
              ["Age", formatAge(channel?.freshness?.age_seconds)],
              ["Quality", channel?.quality?.passed === false ? "failed" : "passed"]
            ]}
          />
        ))}
      </div>
      <LatestRunPanel replay={latestReplay} />
      <FailureTimeline reasons={data?.failure_reason_timeline ?? []} />
      <JsonPanel title="Runtime Status" result={status} compact />
    </div>
  );
}

function TestFlowPanel({ result }: { result?: SmartQTFApiResult<TestflowPayload> }) {
  const data = result?.data;
  const stages = data?.stages ?? [];
  return (
    <div className="testflow-panel">
      <ErrorNotice result={result} />
      <div className="section-heading">
        <h2>Latest TestFlow</h2>
        <span>{data?.latest_run_replay?.pointer?.run_id ?? data?.reason ?? "Pending"}</span>
      </div>
      <div className="stage-list">
        {stages.length === 0 ? <p className="empty">No stage replay loaded.</p> : null}
        {stages.map((stage) => (
          <article className="stage-entry" key={stage.stage ?? "stage"}>
            <div>
              <span>{stage.stage ?? "stage"}</span>
              <strong>{stage.status ?? "unknown"}</strong>
              <p>{stage.error ?? stage.skip_reason ?? firstReason(stage.summary?.reason_codes) ?? "completed"}</p>
              <ReasonCodeList reasons={stage.summary?.reason_codes ?? []} />
            </div>
            <div className="stage-meta">
              <span>{formatAge(stage.summary?.duration_seconds)}</span>
              <span>{`${String(stage.summary?.input_keys?.length ?? 0)} in / ${String(stage.summary?.output_keys?.length ?? 0)} out`}</span>
            </div>
          </article>
        ))}
      </div>
      <FailureTimeline reasons={data?.failure_reason_timeline ?? []} />
      <JsonPanel title="TestFlow Payload" result={result} compact />
    </div>
  );
}

function LatestRunPanel({ replay }: { replay?: RuntimeStatus["latest_run_replay"] }) {
  const stages = replay?.stage_summaries ?? [];
  if (!replay) {
    return null;
  }
  return (
    <div className="latest-run-panel" aria-label="Latest run replay">
      <div className="section-heading">
        <h2>Latest Run</h2>
        <span>{replay.pointer?.run_id ?? "Pending"}</span>
      </div>
      <div className="stage-chip-row">
        {stages.map((stage) => (
          <span key={stage.stage ?? "stage"} className={stage.status === "succeeded" ? "stage-chip ok" : "stage-chip"}>
            {stage.stage ?? "stage"}:{stage.status ?? "unknown"}
          </span>
        ))}
      </div>
    </div>
  );
}

function FailureTimeline({ reasons }: { reasons: RuntimeStatus["failure_reason_timeline"] }) {
  const timeline = reasons ?? [];
  return (
    <div className="failure-timeline" aria-label="Failure reason timeline">
      <div className="section-heading">
        <h2>Failure Timeline</h2>
        <span>{timeline.length === 0 ? "Clear" : `${timeline.length} item(s)`}</span>
      </div>
      {timeline.length === 0 ? <p className="empty">No skip, rejection, or error reason in the latest replay.</p> : null}
      {timeline.map((item, index) => (
        <article className="timeline-entry" key={`${item.stage ?? "stage"}-${item.at ?? index}`}>
          <div>
            <span>{item.stage ?? "worker"}</span>
            <strong>{item.reason ?? item.status ?? "reason"}</strong>
            <p>{item.run_id ?? [item.symbol, item.timeframe].filter(Boolean).join(" / ")}</p>
            <ReasonCodeList reasons={item.reason_codes ?? []} />
          </div>
          <span>{formatTimestamp(item.at ?? undefined)}</span>
        </article>
      ))}
    </div>
  );
}

function LogsPanel({
  result,
  filters,
  onFiltersChange,
  onRefresh
}: {
  result?: SmartQTFApiResult<LogsPayload>;
  filters: { runId: string; symbol: string; timeframe: string };
  onFiltersChange: (filters: { runId: string; symbol: string; timeframe: string }) => void;
  onRefresh: () => void;
}) {
  const events = result?.data?.events ?? [];
  return (
    <div>
      <ErrorNotice result={result} />
      <div className="filter-row" aria-label="Log filters">
        <input
          aria-label="Run id filter"
          value={filters.runId}
          onChange={(event) => onFiltersChange({ ...filters, runId: event.target.value })}
          placeholder="run_id"
        />
        <input
          aria-label="Symbol filter"
          value={filters.symbol}
          onChange={(event) => onFiltersChange({ ...filters, symbol: event.target.value })}
          placeholder="symbol"
        />
        <input
          aria-label="Timeframe filter"
          value={filters.timeframe}
          onChange={(event) => onFiltersChange({ ...filters, timeframe: event.target.value })}
          placeholder="timeframe"
        />
        <button type="button" onClick={onRefresh}>
          Apply
        </button>
      </div>
      <div className="log-list">
        {events.length === 0 ? <p className="empty">No worker events loaded.</p> : null}
        {events.map((event, index) => (
          <article className="log-entry" key={`${event.type ?? "event"}-${index}`}>
            <div className="log-entry-header">
              <span>{event.type ?? "event"}</span>
              <span>{formatTimestamp(event.created_at)}</span>
            </div>
            <p>{event.message ?? "No message"}</p>
          </article>
        ))}
      </div>
    </div>
  );
}

function OptimizationPanel({ result }: { result?: SmartQTFApiResult<OptimizationPayload> }) {
  const data = result?.data;
  const evidence = data?.evidence_summary ?? {};
  const artifacts = data?.artifact_summaries ?? [];
  const candidates = data?.review_candidates ?? [];
  const reasonCodes = data?.reason_codes ?? [];
  const safety = data?.safety ?? {};
  const isSkipped = data?.status === "SKIPPED" || data?.artifact_count === 0;
  const gateMessage = stringValue(data?.reason) ?? stringValue(data?.latest_report?.message) ?? "Latest validation report loaded.";
  const [reviewNote, setReviewNote] = useState("");
  const [reviewBusy, setReviewBusy] = useState<string | null>(null);
  const [reviewResult, setReviewResult] = useState<SmartQTFApiResult<SmartQTFJson> | null>(null);

  const submitReview = async (candidate: PromotionReviewCandidate, action: "approve" | "reject") => {
    if (!candidate.artifact_id) {
      return;
    }
    setReviewBusy(`${action}:${candidate.artifact_id}`);
    try {
      const response = await callSmartQTF("/api/smartqtf/optimization/review", {
        method: "POST",
        body: {
          action,
          artifact_id: candidate.artifact_id,
          reviewer_note: reviewNote,
          reviewer: "web-runtime-console",
          dry_run: true,
          manual_review: true
        }
      });
      setReviewResult(response);
    } finally {
      setReviewBusy(null);
    }
  };

  return (
    <div className="optimization-panel">
      <ErrorNotice result={result} />
      <ErrorNotice result={reviewResult ?? undefined} />
      <div className="section-heading">
        <h2>Optimization</h2>
        <span>{data?.latest_report_found ? "Latest report loaded" : "Awaiting validation artifacts"}</span>
      </div>
      <div className="summary-row">
        <StatusBox title="Validation" result={result} fallback="Pending" />
        <article className="status-box">
          <span>Gate</span>
          <strong className={data?.review_status === "READY_FOR_REVIEW" ? "healthy" : ""}>
            {data?.review_status ?? data?.status ?? "Pending"}
          </strong>
          <p>{gateMessage}</p>
        </article>
        <article className="status-box">
          <span>Artifacts</span>
          <strong className={data?.artifact_count ? "healthy" : ""}>{String(data?.artifact_count ?? 0)}</strong>
          <p>{isSkipped ? "Missing validation artifacts" : `${String(data?.failed_count ?? 0)} failed`}</p>
        </article>
      </div>

      <div className="detail-grid" aria-label="Validation artifact detail">
        <DetailBox
          title="OOS Evidence"
          status={truthy(evidence.has_out_of_sample) ? "Present" : "Missing"}
          rows={[
            ["Slices", String(evidence.out_of_sample_count ?? 0)],
            ["Reason", truthy(evidence.has_out_of_sample) ? "out_of_sample_validation_present" : "missing_out_of_sample_validation"]
          ]}
        />
        <DetailBox
          title="Walk-Forward Evidence"
          status={Number(evidence.walk_forward_count ?? 0) > 0 ? "Present" : "Missing"}
          rows={[
            ["Windows", String(evidence.walk_forward_count ?? 0)],
            ["Passes", String(evidence.walk_forward_pass_count ?? 0)],
            [
              "Reason",
              Number(evidence.walk_forward_count ?? 0) > 0 ? "walk_forward_validation_present" : "missing_walk_forward_validation"
            ]
          ]}
        />
        <DetailBox
          title="Monte Carlo Evidence"
          status={truthy(evidence.has_monte_carlo) ? "Present" : "Missing"}
          rows={[
            ["Survival", stringValue(evidence.monte_carlo_survival_rate_min) ?? String(evidence.monte_carlo_survival_rate_min ?? "Missing")],
            ["Reason", truthy(evidence.has_monte_carlo) ? "monte_carlo_validation_present" : "missing_monte_carlo_validation"]
          ]}
        />
        <DetailBox
          title="Safety"
          status="Dry-run"
          rows={[
            ["Live orders", yesNo(safety.live_orders_sent)],
            ["Analytics live state", yesNo(safety.analytics_modified_live_state)],
            ["Key material", yesNo(safety.key_material_detected ?? safety.contains_real_credentials)]
          ]}
        />
      </div>

      <div className="review-panel" aria-label="Promotion review">
        <div className="section-heading">
          <h2>Promotion Review</h2>
          <span>{data?.manual_review_required ? "Manual gate" : "Dry-run only"}</span>
        </div>
        <textarea
          aria-label="Promotion review note"
          value={reviewNote}
          onChange={(event) => setReviewNote(event.target.value)}
          placeholder="Review note"
          rows={3}
        />
        <div className="review-list">
          {candidates.length === 0 ? <p className="empty">No candidate is ready for manual review.</p> : null}
          {candidates.map((candidate) => (
            <article className="review-entry" key={candidate.artifact_id ?? candidate.candidate_version ?? "candidate"}>
              <div>
                <span>{candidate.symbol ?? "symbol"}</span>
                <strong>{candidate.strategy_id ?? "strategy"} / {candidate.candidate_version ?? "candidate"}</strong>
                <p>{candidate.review_status ?? candidate.status ?? "UNKNOWN"}</p>
                <ReasonCodeList reasons={candidate.reason_codes ?? candidate.gate_decision?.reason_codes ?? []} />
              </div>
              <div className="review-actions">
                <button
                  type="button"
                  onClick={() => submitReview(candidate, "approve")}
                  disabled={!candidate.approve_enabled || reviewBusy !== null}
                >
                  Approve
                </button>
                <button
                  type="button"
                  onClick={() => submitReview(candidate, "reject")}
                  disabled={!candidate.reject_enabled || reviewBusy !== null}
                >
                  Reject
                </button>
              </div>
            </article>
          ))}
        </div>
      </div>

      <div className="evidence-grid" aria-label="Validation evidence">
        <Metric label="OOS" value={truthy(evidence.has_out_of_sample) ? String(evidence.out_of_sample_count ?? 0) : "Missing"} />
        <Metric label="Walk-Forward" value={String(evidence.walk_forward_count ?? 0)} />
        <Metric label="WF Passes" value={String(evidence.walk_forward_pass_count ?? 0)} />
        <Metric label="Monte Carlo" value={truthy(evidence.has_monte_carlo) ? String(evidence.monte_carlo_survival_rate_min ?? "Present") : "Missing"} />
      </div>

      {reasonCodes.length > 0 ? (
        <div className="timeframe-row">
          {reasonCodes.map((reason) => (
            <span key={reason}>{reason}</span>
          ))}
        </div>
      ) : null}

      <div className="artifact-list">
        {artifacts.length === 0 ? <p className="empty">No validation artifacts indexed.</p> : null}
        {artifacts.map((artifact) => (
          <article className="artifact-entry" key={artifact.path ?? artifact.artifact_id ?? artifact.candidate_version ?? "artifact"}>
            <div>
              <span>{artifact.symbol ?? "symbol"}</span>
              <strong>{artifact.strategy_id ?? "strategy"} / {artifact.candidate_version ?? "candidate"}</strong>
              <p>{artifact.message ?? "Validation artifact summary loaded."}</p>
              <div className="artifact-evidence-row">
                <span>{truthy(artifact.evidence?.has_out_of_sample) ? "OOS present" : "missing_out_of_sample_validation"}</span>
                <span>
                  {Number(artifact.evidence?.walk_forward_count ?? 0) > 0
                    ? `${String(artifact.evidence?.walk_forward_count ?? 0)} WF windows`
                    : "missing_walk_forward_validation"}
                </span>
                <span>{truthy(artifact.evidence?.has_monte_carlo) ? "Monte Carlo present" : "missing_monte_carlo_validation"}</span>
              </div>
              <ReasonCodeList reasons={[...(artifact.reason_codes ?? []), ...(artifact.promotion_decision?.reason_codes ?? [])]} />
            </div>
            <span className={artifact.status === "PASS" ? "pill pass" : "pill"}>{artifact.status ?? "UNKNOWN"}</span>
          </article>
        ))}
      </div>

      <JsonPanel title="Optimization Payload" result={result} compact />
    </div>
  );
}

function JsonPanel<T>({
  title,
  result,
  compact = false
}: {
  title: string;
  result?: SmartQTFApiResult<T>;
  compact?: boolean;
}) {
  return (
    <div className={compact ? "json-block compact" : "json-block"}>
      <div className="section-heading">
        <h2>{title}</h2>
        <span>{result ? `HTTP ${result.status}` : "Pending"}</span>
      </div>
      <ErrorNotice result={result} />
      <pre>{JSON.stringify(result?.data ?? result ?? null, null, 2)}</pre>
    </div>
  );
}

function StatusBox<T>({
  title,
  result,
  fallback = "Pending"
}: {
  title: string;
  result?: SmartQTFApiResult<T>;
  fallback?: string;
}) {
  const healthy = Boolean(result?.ok);
  const label = result ? (healthy ? "OK" : "Check") : fallback;
  return (
    <article className="status-box">
      <span>{title}</span>
      <strong className={healthy ? "healthy" : ""}>{label}</strong>
      {result?.detail || result?.error ? <p>{result.detail ?? result.error}</p> : null}
    </article>
  );
}

function DetailBox({ title, status, rows }: { title: string; status: string; rows: Array<[string, string]> }) {
  return (
    <article className="detail-box">
      <div className="detail-box-header">
        <span>{title}</span>
        <strong>{status}</strong>
      </div>
      <dl>
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </article>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <article className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function ReasonCodeList({ reasons }: { reasons: string[] }) {
  const uniqueReasons = Array.from(new Set(reasons.filter(Boolean)));
  if (uniqueReasons.length === 0) {
    return null;
  }
  return (
    <div className="reason-code-list">
      {uniqueReasons.map((reason) => (
        <span key={reason}>{reason}</span>
      ))}
    </div>
  );
}

function ErrorNotice<T>({ result }: { result?: SmartQTFApiResult<T> }) {
  if (!result || result.ok) {
    return null;
  }
  return (
    <div className="error-notice">
      <strong>{result.error ?? "request_failed"}</strong>
      <span>{result.detail ?? "The worker response could not be loaded."}</span>
    </div>
  );
}

function formatTimestamp(value: SmartQTFJson | undefined) {
  if (typeof value !== "number") {
    return "";
  }
  return new Date(value * 1000).toLocaleTimeString();
}

function formatAge(value: SmartQTFJson | undefined) {
  if (typeof value !== "number") {
    return "pending";
  }
  if (value < 60) {
    return `${Math.round(value)}s`;
  }
  if (value < 3600) {
    return `${Math.round(value / 60)}m`;
  }
  return `${Math.round(value / 3600)}h`;
}

function truthy(value: SmartQTFJson | undefined) {
  return value === true || value === "true" || (typeof value === "number" && value > 0);
}

function stringValue(value: SmartQTFJson | undefined) {
  return typeof value === "string" ? value : undefined;
}

function yesNo(value: SmartQTFJson | undefined) {
  return truthy(value) ? "true" : "false";
}

function firstReason(reasons: string[] | undefined) {
  return reasons && reasons.length > 0 ? reasons[0] : undefined;
}
