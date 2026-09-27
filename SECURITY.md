# Security Policy

Cortex handles API keys, provider credentials, and customer data. We take
reports seriously and respond quickly.

## Reporting a vulnerability

**Do not open a public issue, discussion, or pull request for a security
problem.**

Report it privately through GitHub:
[Report a vulnerability](https://github.com/Celestra-tech/cortex/security/advisories/new).

Include as much of the following as you can:

- The affected component and version or commit.
- A description of the issue and its impact.
- Steps to reproduce, or a proof of concept.
- Any logs, with `X-Request-ID` values, and with secrets removed.

## What to expect

| Stage                   | Target                            |
| ----------------------- | --------------------------------- |
| Acknowledgement         | Within 2 business days            |
| Initial assessment      | Within 5 business days            |
| Fix for critical issues | Within 30 days, often much sooner |
| Public advisory         | After a fix is released           |

We will keep you informed throughout, credit you in the advisory unless you
prefer otherwise, and coordinate disclosure timing with you. We ask that you
give us a reasonable chance to release a fix before disclosing publicly.

## Supported versions

Cortex is in alpha. Security fixes are made on `main` and shipped in the next
release. Once 1.0 is released, the latest minor version receives security
fixes.

| Version        | Supported |
| -------------- | --------- |
| `main`         | Yes       |
| Latest release | Yes       |
| Older releases | No        |

## Scope

In scope: the API, dashboard, SDKs, Docker images, and the deployment
configuration in this repository.

Out of scope: vulnerabilities in third-party model providers, findings that
require a compromised host or stolen credentials, denial of service through
volume alone, and reports from automated scanners without a demonstrated
impact.

## Security model

- API keys are random, prefixed `ctx_`, and stored only as SHA-256 hashes.
  Keys carry a role, can expire, and rotate with an overlap window.
- Every tenant query is scoped to the organization of the authenticated key.
- Production configuration refuses to start with authentication disabled,
  wildcard CORS, default database credentials, or a weak admin token.
- Provider credentials and secrets live in the platform secret manager and
  are referenced, never copied, by deployment manifests.
- Responses carry strict security headers; request bodies are size-limited;
  requests are rate limited per key.

Operators deploying Cortex should also read
[docs/deployment.md](docs/deployment.md).
