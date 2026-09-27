# Chatbot

A terminal chatbot with memory, in about a hundred lines of TypeScript.

Each message streams through the Cortex router. Because the request names a
conversation, Cortex prepends the recent history and the most relevant
long-term memories before calling the model, then stores the exchange. The
application keeps no state of its own.

Complete the [setup](../README.md#setup) first, then:

```bash
pnpm --filter @celestra/cortex-example-chatbot start
```

```text
CELESTRA Cortex chatbot. /remember, /recall, /new, /exit.
conversation 0198f7a2-...

you> /remember I am vegetarian and live in Lisbon.
remembered (0198f7a3-...)

you> /new
conversation 0198f7a4-...

you> Suggest a dinner place near me.
cortex> Since you are vegetarian and in Lisbon, try ...
  [anthropic/claude-sonnet-4-5 | 512 tokens | $0.002310]
```

| Command            | Effect                                                            |
| ------------------ | ----------------------------------------------------------------- |
| `/remember <fact>` | Stores a long-term memory, recalled in any conversation           |
| `/recall <query>`  | Searches long-term memories, ranked by relevance, importance, age |
| `/new`             | Starts a new conversation; long-term memories carry over          |
| `/exit`            | Quits                                                             |

Set `CORTEX_OBJECTIVE` to `quality`, `speed`, or `cost` to change how the router
chooses a model (default `balanced`).
