import { NextRequest, NextResponse } from "next/server";
import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import path from "node:path";
import type { SmartQTFApiResult, SmartQTFJson } from "./smartqtf-types";

const DEFAULT_WORKER_URL = "http://127.0.0.1:6667";
const DEFAULT_WORKER_CONFIG_PATH =
  process.env.SMARTQTF_WORKER_CONFIG ?? path.join(repositoryRoot(), "config/examples/paper-runtime.example.json");
const SECRET_KEY_FRAGMENTS = [
  "api_key",
  "apikey",
  "authorization",
  "credential",
  "passphrase",
  "password",
  "private_key",
  "secret",
  "signature",
  "token"
];

export type WorkerProxyOptions = {
  method?: "GET" | "POST";
  path: string;
  request?: NextRequest;
  body?: SmartQTFJson;
};

export async function proxyWorkerJson(options: WorkerProxyOptions) {
  const method = options.method ?? "GET";
  const workerUrl = workerBaseUrl();
  const url = new URL(options.path, `${workerUrl}/`);

  if (options.request) {
    const incomingUrl = new URL(options.request.url);
    incomingUrl.searchParams.forEach((value, key) => {
      url.searchParams.set(key, value);
    });
  }

  let requestBody = options.body;
  if (method !== "GET" && requestBody === undefined && options.request) {
    requestBody = await readJsonBody(options.request);
  }
  requestBody = withDefaultWorkerConfig(options.path, requestBody);

  try {
    const response = await requestWorkerJson(url, {
      method,
      headers: {
        Accept: "application/json",
        ...(method === "GET" ? {} : { "Content-Type": "application/json" })
      },
      body: method === "GET" ? undefined : JSON.stringify(requestBody ?? {})
    });

    const payload = sanitizeSecrets(readResponseJson(response.body)) ?? null;
    const result: SmartQTFApiResult = {
      ok: response.status >= 200 && response.status < 300,
      status: response.status,
      data: payload
    };
    if (!result.ok) {
      result.detail = extractDetail(payload) ?? response.statusText;
    }
    return NextResponse.json(result, { status: result.ok ? 200 : response.status });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return NextResponse.json(
      {
        ok: false,
        status: 502,
        error: "worker_unreachable",
        detail: message,
        workerUrl
      } satisfies SmartQTFApiResult,
      { status: 502 }
    );
  }
}

type WorkerHttpRequestOptions = {
  method: "GET" | "POST";
  headers: Record<string, string>;
  body?: string;
};

type WorkerHttpResponse = {
  status: number;
  statusText: string;
  body: string;
};

function requestWorkerJson(url: URL, options: WorkerHttpRequestOptions): Promise<WorkerHttpResponse> {
  return new Promise((resolve, reject) => {
    const requestImpl = url.protocol === "https:" ? httpsRequest : httpRequest;
    const request = requestImpl(
      url,
      {
        method: options.method,
        headers: {
          ...options.headers,
          ...(options.body === undefined ? {} : { "Content-Length": Buffer.byteLength(options.body).toString() })
        },
        timeout: 10_000
      },
      (response) => {
        const chunks: Buffer[] = [];
        response.on("data", (chunk: Buffer | string) => {
          chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
        });
        response.on("end", () => {
          resolve({
            status: response.statusCode ?? 502,
            statusText: response.statusMessage ?? "",
            body: Buffer.concat(chunks).toString("utf8")
          });
        });
      }
    );

    request.on("error", reject);
    request.on("timeout", () => {
      request.destroy(new Error("worker request timed out"));
    });

    if (options.body !== undefined) {
      request.write(options.body);
    }
    request.end();
  });
}

export function workerBaseUrl() {
  return normalizeBaseUrl(process.env.SMARTQTF_WORKER_URL ?? DEFAULT_WORKER_URL);
}

async function readJsonBody(request: NextRequest): Promise<SmartQTFJson | undefined> {
  try {
    return (await request.json()) as SmartQTFJson;
  } catch {
    return {};
  }
}

function readResponseJson(text: string): SmartQTFJson {
  if (!text) {
    return null;
  }
  try {
    return JSON.parse(text) as SmartQTFJson;
  } catch {
    return { detail: text };
  }
}

function normalizeBaseUrl(value: string) {
  return value.endsWith("/") ? value : `${value}/`;
}

function repositoryRoot() {
  const cwd = process.cwd();
  return path.basename(cwd) === "web" ? path.dirname(cwd) : cwd;
}

function withDefaultWorkerConfig(proxyPath: string, value: SmartQTFJson | undefined): SmartQTFJson | undefined {
  if (proxyPath !== "/start" && proxyPath !== "/run-once") {
    return value;
  }
  const payload = isJsonObject(value) ? { ...value } : {};
  if (typeof payload.config_path !== "string" || payload.config_path.length === 0) {
    payload.config_path = DEFAULT_WORKER_CONFIG_PATH;
  }
  return payload;
}

function extractDetail(payload: SmartQTFJson): string | undefined {
  if (payload && typeof payload === "object" && !Array.isArray(payload)) {
    const detail = payload.detail ?? payload.error ?? payload.reason;
    return typeof detail === "string" ? detail : undefined;
  }
  return undefined;
}

function isJsonObject(value: SmartQTFJson | undefined): value is Record<string, SmartQTFJson | undefined> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function sanitizeSecrets(value: SmartQTFJson | undefined): SmartQTFJson | undefined {
  if (Array.isArray(value)) {
    return value.map((item) => sanitizeSecrets(item) ?? null);
  }
  if (value && typeof value === "object") {
    const sanitized: Record<string, SmartQTFJson | undefined> = {};
    for (const [key, item] of Object.entries(value)) {
      if (isSecretKey(key)) {
        if (key === "contains_real_credentials" && typeof item === "boolean") {
          sanitized.key_material_detected = item;
        }
        continue;
      }
      const nextValue = sanitizeSecrets(item);
      if (nextValue !== undefined) {
        sanitized[key] = nextValue;
      }
    }
    return sanitized;
  }
  return value;
}

function isSecretKey(key: string) {
  const normalized = key.toLowerCase();
  return SECRET_KEY_FRAGMENTS.some((fragment) => normalized.includes(fragment));
}
