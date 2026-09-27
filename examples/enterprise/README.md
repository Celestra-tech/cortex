# Enterprise onboarding

How a platform team brings a new tenant onto Cortex, end to end, in one Python
script:

1. **Organization.** Create the tenant with the operator's admin token, which
   returns its first admin API key. Or start from an existing admin key.
2. **Scoped credentials.** Mint a `member` key that expires in 90 days for the
   workload. The admin key never leaves the onboarding process.
3. **Knowledge.** Ingest the tenant's policy documents as the workload.
4. **Grounded answers.** Ask a question that needs both documents; print the
   answer, retrieval confidence, and which citations the model used.
5. **Audit.** List every provider call behind the answer, with attempts,
   fallbacks, latency, tokens, and cost.
6. **Rotation.** Rotate the workload key with a five-minute overlap, then
   revoke both keys and delete the documents (skip cleanup with `--keep`).

## Run

Against a new organization (the API must have `CORTEX_ADMIN_TOKEN` set):

```bash
CORTEX_ADMIN_TOKEN=... uv run python examples/enterprise/main.py --slug northwind
```

Against an existing organization, with an admin API key:

```bash
CORTEX_API_KEY=ctx_... uv run python examples/enterprise/main.py
```

`CORTEX_BASE_URL` selects the API (default `http://localhost:8000`).

## Output

```text
== 1. Organization
organization Northwind (0198f7a2-...)
acting as admin of Northwind (northwind)

== 2. Scoped workload key
member key ctx_ti6Mrl-3... expires 2026-12-26

== 3. Ingest documents
indexed expense-policy.md: 5 chunks
indexed security-policy.md: 5 chunks

== 4. Grounded answer
Q: Who has to approve a 3,000 USD purchase, and how soon must I submit the receipt?
A: Your direct manager and your department head must approve it [1]. Submit the
receipt within 30 days of the purchase [2].
retrieval confidence: high (0.81)
 *[1] Northwind Expense Policy > Approval limits
 *[2] Northwind Expense Policy > Submitting expenses

== 5. Audit
attempt 1: anthropic/claude-sonnet-4-5 1432 ms, 612+58 tokens, $0.002706, ok
total anthropic: $0.002706

== 6. Rotate and revoke
rotated to ctx_NU9JCPQ7...; the old key works for 5 more minutes
revoked both keys
```

The organization endpoint (`POST /v1/admin/organizations`) is operator-only
and deliberately not part of the SDKs, so the script calls it with `httpx`.
