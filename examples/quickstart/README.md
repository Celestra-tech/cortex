# Quickstart

The smallest useful Cortex program, in TypeScript and Python. It checks that the
API is ready, lists the models this organization can route to, and sends one
completion, printing the answer with the routing decision, token count,
latency, and cost.

Complete the [setup](../README.md#setup) first.

## TypeScript

```bash
pnpm --filter @celestra/cortex-example-quickstart start
```

## Python

```bash
uv run python examples/quickstart/main.py
```

## Output

```text
Cortex 1.0.0-alpha is healthy
Routable models: openai/gpt-4.1, openai/gpt-4.1-mini, openai/o4-mini

An intelligence infrastructure layer sits between applications and foundation models...

openai/gpt-4.1-mini | auto: openai/gpt-4.1-mini scored 0.75 for objective=balanced among 3 eligible models; next openai/o4-mini 0.47 | 142 tokens | 812 ms | $0.000094
```

## Next

- Name a model with `model: "anthropic/claude-sonnet-4-5"`, or change
  `objective` to `quality`, `speed`, or `cost`.
- Add `knowledge: { top_k: 5 }` to ground the answer in ingested documents.
- See [chatbot](../chatbot/) for streaming and memory.
