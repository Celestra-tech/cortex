---
name: Bug report
about: Report behavior that differs from the documentation or breaks a workflow
title: "fix(<component>): <short description>"
labels: ["bug", "triage"]
---

<!--
Security vulnerabilities must not be reported here. Use
https://github.com/Celestra-tech/cortex/security/advisories/new instead.
-->

## Summary

A clear, one-paragraph description of the problem.

## Component

<!-- Delete all but one. -->

- API
- Router
- Memory
- Knowledge
- Observatory
- TypeScript SDK
- Python SDK
- Dashboard
- Deployment and infrastructure
- Documentation

## Steps to reproduce

1.
2.
3.

A minimal request, script, or repository that reproduces the problem is the
fastest way to a fix.

```bash

```

## Expected behavior

What you expected to happen.

## Actual behavior

What happened instead. Include the full error message and the `X-Request-ID`
from the response when there is one.

```text

```

## Environment

| Item                                                     | Value |
| -------------------------------------------------------- | ----- |
| Cortex version or commit                                 |       |
| Deployment (Docker Compose, Cloud Run, local `pnpm dev`) |       |
| SDK and version                                          |       |
| Runtime (Node, Python)                                   |       |
| Operating system                                         |       |
| Model providers configured                               |       |

## Additional context

Logs, screenshots, or anything else that helps. Remove API keys, provider
credentials, and customer data before posting.
