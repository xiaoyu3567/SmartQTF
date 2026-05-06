import { chromium } from "@playwright/test";

const cdpUrl = process.env.SMARTQTF_PLAYWRIGHT_CDP_URL;
const appUrl = process.env.SMARTQTF_WEB_SMOKE_URL || "http://127.0.0.1:3000";
const consoleUrl = new URL("/smartqtf", appUrl).toString();

if (!cdpUrl) {
  throw new Error("SMARTQTF_PLAYWRIGHT_CDP_URL is required");
}

const SECRET_FRAGMENTS = [
  "api_key",
  "apikey",
  "authorization",
  "credential",
  "passphrase",
  "password",
  "private_key",
  "secret",
  "token",
];

function assertNoSecretFragments(payload, label) {
  const lowered = JSON.stringify(payload).toLowerCase();
  const found = SECRET_FRAGMENTS.find((fragment) => lowered.includes(fragment));
  if (found) {
    throw new Error(`${label} exposed secret-like fragment: ${found}`);
  }
}

const browser = await chromium.connectOverCDP(cdpUrl);

try {
  const context = browser.contexts()[0] ?? (await browser.newContext());
  const page = await context.newPage();
  const response = await page.goto(consoleUrl, { waitUntil: "networkidle", timeout: 30000 });
  if (!response || !response.ok()) {
    throw new Error(`Failed to load ${consoleUrl}: ${response ? response.status() : "no response"}`);
  }

  await page.waitForSelector("h1");
  await page.getByRole("heading", { name: "Runtime Console" }).waitFor();

  for (const label of ["Main", "TestFlow", "Logs", "Optimization"]) {
    await page.getByRole("button", { name: label, exact: true }).waitFor();
  }

  for (const label of ["Start Scan Loop", "Run Once", "Stop", "Refresh"]) {
    await page.getByRole("button", { name: label, exact: true }).waitFor();
  }

  for (const label of ["5m execution", "15m context", "1h context", "4h context"]) {
    await page.getByText(label, { exact: true }).waitFor();
  }

  await page.getByRole("button", { name: "Start Scan Loop", exact: true }).click();
  await page.waitForTimeout(500);
  await page.locator('[aria-label="Worker status"]').getByText("Running", { exact: true }).waitFor();

  await page.getByRole("button", { name: "Run Once", exact: true }).click();
  await page.waitForTimeout(1000);

  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await page.waitForTimeout(500);
  await page.locator('[aria-label="Worker status"]').getByText("Stopped", { exact: true }).waitFor();

  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await page.waitForTimeout(1000);

  await page.getByRole("button", { name: "TestFlow", exact: true }).click();
  await page.getByText("Latest TestFlow").waitFor();

  await page.getByRole("button", { name: "Logs", exact: true }).click();
  await page.locator(".log-entry").filter({ hasText: "run_once" }).first().waitFor({ timeout: 10000 });

  await page.getByRole("button", { name: "Optimization", exact: true }).click();
  await page.getByRole("heading", { name: "Optimization", exact: true }).waitFor();
  for (const label of [
    "OOS Evidence",
    "Walk-Forward Evidence",
    "Monte Carlo Evidence",
    "Safety",
    "missing_out_of_sample_validation",
    "missing_walk_forward_validation",
    "missing_monte_carlo_validation",
  ]) {
    await page.getByText(label, { exact: true }).first().waitFor();
  }

  await page.getByRole("button", { name: "Main", exact: true }).click();
  await page.getByRole("heading", { name: "Runtime Status", exact: true }).waitFor();

  const pageText = (await page.locator("body").innerText()).toLowerCase();
  const secretFragment = SECRET_FRAGMENTS.find((fragment) => pageText.includes(fragment));
  if (secretFragment) {
    throw new Error(`Runtime Console rendered secret-like fragment: ${secretFragment}`);
  }

  const apiChecks = [
    "/api/smartqtf/status",
    "/api/smartqtf/kline?symbol=BTCUSDT&timeframe=5m",
    "/api/smartqtf/testflow",
    "/api/smartqtf/logs?limit=20",
    "/api/smartqtf/optimization",
  ];

  const results = {};
  for (const path of apiChecks) {
    const apiResponse = await page.request.get(new URL(path, appUrl).toString());
    if (!apiResponse.ok()) {
      throw new Error(`API check failed for ${path}: ${apiResponse.status()}`);
    }
    const payload = await apiResponse.json();
    assertNoSecretFragments(payload, path);
    results[path] = payload;
  }

  const klinePayload = results["/api/smartqtf/kline?symbol=BTCUSDT&timeframe=5m"];
  if (klinePayload.ok !== true) {
    throw new Error(`Unexpected kline response state: ${JSON.stringify(klinePayload)}`);
  }
  const contextTimeframes = klinePayload.data?.context_timeframes || [];
  const optimizationPayload = results["/api/smartqtf/optimization"];
  const optimizationData = optimizationPayload.data || {};
  if (optimizationData.artifact_count === 0 && optimizationData.review_status !== "SKIPPED") {
    throw new Error(`Optimization empty-artifact state was not rendered as SKIPPED: ${JSON.stringify(optimizationPayload)}`);
  }
  for (const key of ["live_orders_sent", "analytics_modified_live_state", "key_material_detected"]) {
    if (optimizationData.safety?.[key] !== false) {
      throw new Error(`Optimization safety field ${key} was not false: ${JSON.stringify(optimizationPayload)}`);
    }
  }

  const summary = {
    ok: true,
    console_url: consoleUrl,
    worker_running_after_start: results["/api/smartqtf/status"].data?.running,
    execution_timeframe: klinePayload.data?.execution_timeframe ?? null,
    context_timeframes: contextTimeframes,
    kline_available: klinePayload.data?.available ?? null,
    kline_reason: klinePayload.data?.reason ?? null,
    logs_event_count: results["/api/smartqtf/logs?limit=20"].data?.events?.length ?? 0,
    optimization_available: optimizationData.available ?? null,
    optimization_artifact_count: optimizationData.artifact_count ?? null,
    optimization_review_status: optimizationData.review_status ?? null,
  };

  console.log(JSON.stringify(summary, null, 2));
} finally {
  await browser.close();
}
