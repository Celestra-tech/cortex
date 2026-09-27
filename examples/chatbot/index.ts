/**
 * A terminal chatbot with persistent memory. Every turn streams through the
 * router; Cortex prepends conversation history and relevant long-term
 * memories, then stores the exchange. Reads CORTEX_API_KEY and CORTEX_BASE_URL.
 *
 *   pnpm --filter @celestra/cortex-example-chatbot start
 *
 * Commands:
 *   /remember <fact>   store a long-term memory (recalled in later conversations)
 *   /recall <query>    search long-term memories
 *   /new               start a new conversation (memories carry over)
 *   /exit              quit
 */
import { createInterface } from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";

import { Cortex, type Objective } from "@celestra/cortex-sdk";

const SYSTEM_PROMPT =
  "You are a helpful assistant. Use what you remember about the user when it is relevant, " +
  "and say so when you do not know something.";

const cortex = new Cortex();
const objective = (process.env.CORTEX_OBJECTIVE ?? "balanced") as Objective;
const terminal = createInterface({ input, output });
// Attached immediately so lines typed (or piped) during a network call are queued, not dropped.
const lines = terminal[Symbol.asyncIterator]();

async function newConversation(): Promise<string> {
  const conversation = await cortex.memory.createConversation({
    title: `Terminal chat ${new Date().toISOString()}`,
  });
  console.log(`conversation ${conversation.id}\n`);
  return conversation.id;
}

async function reply(conversationId: string, content: string): Promise<void> {
  const stream = cortex.chat.stream({
    objective,
    messages: [
      { role: "system", content: SYSTEM_PROMPT },
      { role: "user", content },
    ],
    memory: { conversation_id: conversationId },
  });

  output.write("cortex> ");
  for await (const event of stream) {
    switch (event.type) {
      case "token":
        output.write(event.delta);
        break;
      case "complete": {
        const { provider, model, tokens, cost_estimate } = event.completion;
        output.write(
          `\n  [${provider}/${model} | ${tokens.total} tokens | $${cost_estimate.toFixed(6)}]\n\n`,
        );
        break;
      }
      case "error":
        output.write(`\n  [error: ${event.error.message}]\n\n`);
        break;
      case "start":
        break;
    }
  }
}

/** Handles one line of input; false means quit. */
async function handle(line: string, state: { conversationId: string }): Promise<boolean> {
  const [command, ...rest] = line.split(" ");
  const argument = rest.join(" ").trim();

  switch (command) {
    case "/exit":
      return false;
    case "/new":
      state.conversationId = await newConversation();
      break;
    case "/remember": {
      if (!argument) {
        console.log("usage: /remember <fact>\n");
        break;
      }
      const memory = await cortex.memory.storeMemory({
        type: "semantic",
        content: argument,
        importance: 0.8,
      });
      console.log(`remembered (${memory.id})\n`);
      break;
    }
    case "/recall": {
      const { memories } = await cortex.memory.searchMemories({ query: argument || undefined });
      if (memories.length === 0) console.log("nothing remembered yet");
      for (const memory of memories) {
        console.log(`  ${memory.score.toFixed(2)}  [${memory.type}] ${memory.content}`);
      }
      console.log();
      break;
    }
    default:
      await reply(state.conversationId, line);
  }
  return true;
}

async function main(): Promise<void> {
  console.log("CELESTRA Cortex chatbot. /remember, /recall, /new, /exit.");
  const state = { conversationId: await newConversation() };

  terminal.setPrompt("you> ");
  for (;;) {
    terminal.prompt();
    const next = await lines.next();
    if (next.done) break;
    const line = next.value.trim();
    if (line && !(await handle(line, state))) break;
  }
  terminal.close();
}

await main();
