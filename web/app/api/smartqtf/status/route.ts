import { proxyWorkerJson } from "@/lib/smartqtf-api";

export const dynamic = "force-dynamic";

export async function GET() {
  return proxyWorkerJson({ path: "/status" });
}
