import type { SmartQTFApiResult, SmartQTFJson } from "./smartqtf-types";

export async function callSmartQTF<T = SmartQTFJson>(
  path: string,
  options: { method?: "GET" | "POST"; body?: SmartQTFJson } = {}
): Promise<SmartQTFApiResult<T>> {
  const method = options.method ?? "GET";
  const response = await fetch(path, {
    method,
    headers: {
      Accept: "application/json",
      ...(method === "GET" ? {} : { "Content-Type": "application/json" })
    },
    body: method === "GET" ? undefined : JSON.stringify(options.body ?? {})
  });
  const payload = (await response.json()) as SmartQTFApiResult<T>;
  return {
    ...payload,
    ok: response.ok && payload.ok !== false,
    status: payload.status ?? response.status
  };
}
