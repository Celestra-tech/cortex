import { connection } from "next/server";

import { Dashboard } from "@/components/dashboard";
import { getServerConfig } from "@/lib/config";
import { getSystemStatus } from "@/lib/system-status";

export default async function HomePage() {
  // Status must reflect the live system on every request, never a build-time snapshot.
  await connection();
  const status = await getSystemStatus();
  const { apiPublicUrl } = getServerConfig();

  return <Dashboard status={status} apiDocsUrl={`${apiPublicUrl}/docs`} />;
}
