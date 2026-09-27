/**
 * The smallest useful Cortex program: check the API, see what can be routed
 * to, and send one completion. Reads CORTEX_API_KEY and CORTEX_BASE_URL.
 *
 *   pnpm --filter @celestra/cortex-example-quickstart start
 */
import { Cortex, CortexError } from "@celestra/cortex-sdk";

const cortex = new Cortex();

const readiness = await cortex.system.readiness();
console.log(`Cortex ${readiness.version} is ${readiness.status}`);

const { models } = await cortex.router.models();
const routable = models.filter((model) => model.available && model.allowed);
if (routable.length === 0) {
  console.error(
    "No models are routable. Configure a provider key on the API, e.g. OPENAI_API_KEY.",
  );
  process.exit(1);
}
console.log(`Routable models: ${routable.map((model) => model.id).join(", ")}`);

try {
  const completion = await cortex.chat.complete({
    objective: "balanced",
    messages: [
      { role: "system", content: "Answer in one short paragraph." },
      { role: "user", content: "What does an intelligence infrastructure layer do?" },
    ],
  });

  console.log(`\n${completion.output}\n`);
  console.log(
    [
      `${completion.provider}/${completion.model}`,
      completion.routing_reason,
      `${completion.tokens.total} tokens`,
      `${Math.round(completion.latency_ms)} ms`,
      `$${completion.cost_estimate.toFixed(6)}`,
    ].join(" | "),
  );
} catch (error) {
  if (error instanceof CortexError) {
    console.error(`Cortex rejected the request: ${error.message}`);
    process.exit(1);
  }
  throw error;
}
