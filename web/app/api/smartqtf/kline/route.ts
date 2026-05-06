import { NextRequest } from "next/server";
import { proxyWorkerJson } from "@/lib/smartqtf-api";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  return proxyWorkerJson({ path: "/kline", request });
}
