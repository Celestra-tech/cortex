# Examples

Runnable programs built on the Cortex SDKs. Each one works against a local API
(`docker compose up`, or `pnpm dev` from the repository root) or any deployed
Cortex.

| Example                     | Language           | Shows                                                                                 |
| --------------------------- | ------------------ | ------------------------------------------------------------------------------------- |
| [quickstart](./quickstart/) | TypeScript, Python | Readiness, the model catalog, and one routed completion                               |
| [chatbot](./chatbot/)       | TypeScript         | Streaming chat with conversation memory and long-term recall                          |
| [enterprise](./enterprise/) | Python             | Tenant onboarding: scoped keys, document ingestion, grounded answers, audit, rotation |

## Setup

From the repository root:

```bash
pnpm install
uv sync --all-packages
```

Create an organization and an API key (the secret prints once):

```bash
cd apps/api
uv run python -m cortex_api.cli create-organization --name "Acme" --slug acme
uv run python -m cortex_api.cli create-api-key --organization acme --name examples --role admin
```

Then export the key, and the API URL if it is not `http://localhost:8000`:

```bash
export CORTEX_API_KEY=ctx_...
export CORTEX_BASE_URL=http://localhost:8000
```

Completions need at least one provider key on the API (`OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, or `GEMINI_API_KEY`); see [.env.example](../.env.example).
