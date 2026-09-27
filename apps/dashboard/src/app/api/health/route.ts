import { DASHBOARD_SERVICE, DASHBOARD_VERSION } from "@/lib/config";

export function GET() {
  return Response.json(
    { status: "healthy", service: DASHBOARD_SERVICE, version: DASHBOARD_VERSION },
    { headers: { "Cache-Control": "no-store" } },
  );
}
