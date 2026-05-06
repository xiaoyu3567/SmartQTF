import { NextRequest } from "next/server";
import { proxyWorkerJson } from "@/lib/smartqtf-api";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  return proxyWorkerJson({ method: "POST", path: "/stop", request });
}
