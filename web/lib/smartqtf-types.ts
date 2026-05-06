export type SmartQTFJson =
  | null
  | boolean
  | number
  | string
  | SmartQTFJson[]
  | { [key: string]: SmartQTFJson | undefined };

export type SmartQTFApiResult<T = SmartQTFJson> = {
  ok: boolean;
  status: number;
  data?: T;
  error?: string;
  detail?: string;
  workerUrl?: string;
};

export type WorkerEvent = {
  type?: string;
  message?: string;
  at?: number;
  created_at?: number;
  metadata?: Record<string, SmartQTFJson>;
  [key: string]: SmartQTFJson | undefined;
};

export type LatestReportPointer = {
  batch_id?: string | null;
  run_id?: string | null;
  symbol?: string | null;
  timeframe?: string | null;
  source?: string | null;
  started_at?: number | null;
  finished_at?: number | null;
  success?: boolean | null;
  [key: string]: SmartQTFJson | undefined;
};

export type FailureReason = {
  stage?: string | null;
  status?: string | null;
  reason?: string | null;
  reason_codes?: string[];
  at?: number | null;
  run_id?: string | null;
  symbol?: string | null;
  timeframe?: string | null;
  [key: string]: SmartQTFJson | undefined;
};

export type ScanLoopHealth = {
  state?: string;
  running?: boolean;
  thread_alive?: boolean;
  poll_interval_seconds?: number;
  scan_interval_seconds?: number | null;
  started_at?: number | null;
  stopped_at?: number | null;
  last_error?: string | null;
  latest_run_cached_at?: number | null;
  last_run_age_seconds?: number | null;
  latest_report_pointer?: LatestReportPointer | null;
  failure_count?: number;
  safe_mode?: boolean;
  [key: string]: SmartQTFJson | undefined;
};

export type StageSummary = {
  stage?: string | null;
  status?: string | null;
  started_at?: number | null;
  ended_at?: number | null;
  duration_seconds?: number | null;
  has_input?: boolean;
  has_output?: boolean;
  input_keys?: string[];
  output_keys?: string[];
  skip_reason?: string | null;
  error?: string | null;
  reason_codes?: string[];
  [key: string]: SmartQTFJson | undefined;
};

export type LatestRunReplay = {
  pointer?: LatestReportPointer | null;
  batch?: Record<string, SmartQTFJson> | null;
  stage_summaries?: StageSummary[];
  failure_reason_timeline?: FailureReason[];
  final_output_keys?: string[];
  error_count?: number;
  [key: string]: SmartQTFJson | undefined;
};

export type RuntimeSafety = {
  source?: string | null;
  environment_tier?: string | null;
  external_exchange_access?: boolean;
  live_order_submission?: boolean;
  dry_run?: boolean;
  safe_mode?: boolean;
  broker_mode?: string | null;
  [key: string]: SmartQTFJson | undefined;
};

export type RuntimeStatus = {
  service?: string;
  worker_id?: string;
  pid?: number;
  running?: boolean;
  thread_alive?: boolean;
  config_path?: string | null;
  started_at?: number | null;
  stopped_at?: number | null;
  last_error?: string | null;
  latest_batch?: Record<string, SmartQTFJson> | null;
  latest_report?: Record<string, SmartQTFJson> | null;
  latest_report_pointer?: LatestReportPointer | null;
  scan_loop_health?: ScanLoopHealth;
  last_run_age_seconds?: number | null;
  latest_run_replay?: LatestRunReplay | null;
  failure_reason_timeline?: FailureReason[];
  safety?: RuntimeSafety;
  event_count?: number;
  [key: string]: SmartQTFJson | undefined;
};

export type KlineFreshness = {
  reason?: string | null;
  last_timestamp?: number | null;
  age_seconds?: number | null;
  interval_seconds?: number | null;
  stale?: boolean | null;
  cached_at?: number | null;
  status?: string | null;
  stale_timeframes?: string[];
  as_of_timestamp?: number | null;
  last_run_age_seconds?: number | null;
  [key: string]: SmartQTFJson | undefined;
};

export type KlineChannel = {
  timeframe?: string;
  role?: string;
  available?: boolean;
  source?: string;
  bar_count?: number | null;
  bar_limit?: number | null;
  window?: Record<string, SmartQTFJson>;
  quality?: Record<string, SmartQTFJson>;
  freshness?: KlineFreshness;
  coverage?: Record<string, SmartQTFJson>;
  [key: string]: SmartQTFJson | undefined;
};

export type KlineSnapshot = {
  snapshot_id?: string | null;
  source?: string;
  symbol?: string | null;
  execution_timeframe?: string | null;
  context_timeframes?: string[];
  as_of_timestamp?: number | null;
  selected_index?: number | null;
  bar_limits?: Record<string, SmartQTFJson>;
  batches?: Record<string, KlineChannel | undefined>;
  coverage?: Record<string, SmartQTFJson>;
  quality_report?: Record<string, SmartQTFJson> | null;
  alignment?: Record<string, SmartQTFJson>;
  freshness?: KlineFreshness;
  [key: string]: SmartQTFJson | undefined;
};

export type KlinePayload = {
  available?: boolean;
  reason?: string | null;
  symbol?: string | null;
  timeframe?: string | null;
  execution_timeframe?: string | null;
  context_timeframes?: string[];
  worker_cache?: KlineSnapshot | null;
  requested_channel?: KlineChannel | null;
  latest_batch?: Record<string, SmartQTFJson> | null;
  latest_report?: Record<string, SmartQTFJson> | null;
  provider_rest_fallback?: Record<string, SmartQTFJson> | null;
  quality_report?: Record<string, SmartQTFJson> | null;
  [key: string]: SmartQTFJson | undefined;
};

export type LogsPayload = {
  events?: WorkerEvent[];
  count?: number;
  total_matching_count?: number;
  filters?: Record<string, SmartQTFJson>;
  [key: string]: SmartQTFJson | undefined;
};

export type TestflowStage = {
  stage?: string | null;
  status?: string | null;
  started_at?: number | null;
  ended_at?: number | null;
  input_payload?: Record<string, SmartQTFJson>;
  output_payload?: Record<string, SmartQTFJson>;
  skip_reason?: string | null;
  error?: string | null;
  metadata?: Record<string, SmartQTFJson>;
  summary?: StageSummary;
  [key: string]: SmartQTFJson | undefined;
};

export type TestflowPayload = {
  available?: boolean;
  reason?: string | null;
  latest_batch?: Record<string, SmartQTFJson> | null;
  latest_report?: Record<string, SmartQTFJson> | null;
  latest_run_replay?: LatestRunReplay | null;
  stage_count?: number;
  stages?: TestflowStage[];
  multi_timeframe?: Record<string, SmartQTFJson>;
  failure_reason_timeline?: FailureReason[];
  recent_failed_stage?: FailureReason | null;
  status?: RuntimeStatus;
  [key: string]: SmartQTFJson | undefined;
};

export type ValidationEvidenceSummary = {
  has_out_of_sample?: boolean;
  out_of_sample_count?: number;
  walk_forward_count?: number;
  walk_forward_pass_count?: number;
  walk_forward_window_names?: string[];
  has_monte_carlo?: boolean;
  monte_carlo_survival_rate?: number | null;
  monte_carlo_survival_rate_min?: number | null;
  required_evidence?: Record<string, SmartQTFJson>;
  [key: string]: SmartQTFJson | undefined;
};

export type PromotionDecisionSummary = {
  decision?: string | null;
  status?: string | null;
  approved?: boolean | null;
  reason_codes?: string[];
  [key: string]: SmartQTFJson | undefined;
};

export type ValidationArtifactSummary = {
  path?: string;
  status?: string;
  category?: string | null;
  message?: string | null;
  artifact_id?: string | null;
  source_report_id?: string | null;
  symbol?: string | null;
  strategy_id?: string | null;
  candidate_version?: string | null;
  generated_at?: number | null;
  evidence?: ValidationEvidenceSummary;
  promotion_decision?: PromotionDecisionSummary | null;
  reason_codes?: string[];
  [key: string]: SmartQTFJson | undefined;
};

export type PromotionReviewCandidate = {
  artifact_id?: string | null;
  path?: string | null;
  strategy_id?: string | null;
  candidate_version?: string | null;
  symbol?: string | null;
  status?: string;
  review_status?: string;
  approve_enabled?: boolean;
  reject_enabled?: boolean;
  gate_decision?: PromotionDecisionSummary;
  evidence?: ValidationEvidenceSummary;
  reason_codes?: string[];
  latest_manual_review?: Record<string, SmartQTFJson> | null;
  [key: string]: SmartQTFJson | undefined;
};

export type OptimizationPayload = {
  available?: boolean;
  reason?: string | null;
  status?: string;
  review_status?: string;
  artifact_count?: number;
  failed_count?: number;
  latest_report_found?: boolean;
  latest_report?: Record<string, SmartQTFJson> | null;
  artifact_summaries?: ValidationArtifactSummary[];
  review_candidates?: PromotionReviewCandidate[];
  manual_reviews?: Record<string, SmartQTFJson>[];
  manual_review_required?: boolean;
  review_log_path?: string | null;
  evidence_summary?: ValidationEvidenceSummary;
  reason_codes?: string[];
  safety?: {
    live_orders_sent?: boolean;
    analytics_modified_live_state?: boolean;
    key_material_detected?: boolean;
    contains_real_credentials?: boolean;
    network_used?: boolean;
    broker_called?: boolean;
    [key: string]: SmartQTFJson | undefined;
  };
  [key: string]: SmartQTFJson | undefined;
};

export type RuntimeTab = "main" | "testflow" | "logs" | "optimization";
