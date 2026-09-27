"""Onboard a tenant the way a platform team would.

1. Create an organization with the operator's admin token (or reuse an admin key).
2. Mint a scoped, expiring member key for a workload, keeping the admin key out of it.
3. Ingest the organization's documents as that workload.
4. Answer a question grounded in those documents, with citations and confidence.
5. Audit the provider calls behind the answer: attempts, latency, and cost.
6. Rotate the workload key with an overlap window, then revoke it.

    # Bootstrap a new organization (requires CORTEX_ADMIN_TOKEN on the API):
    CORTEX_ADMIN_TOKEN=... uv run python examples/enterprise/main.py --slug northwind

    # Or run against an existing organization with an admin API key:
    CORTEX_API_KEY=ctx_... uv run python examples/enterprise/main.py

Reads CORTEX_BASE_URL (default http://localhost:8000).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

import httpx

from cortex import ConflictError, Cortex, CortexError

DOCUMENTS = Path(__file__).parent / "documents"
QUESTION = "Who has to approve a 3,000 USD purchase, and how soon must I submit the receipt?"


def step(title: str) -> None:
    print(f"\n== {title}")


def bootstrap_organization(base_url: str, admin_token: str, name: str, slug: str) -> str:
    """Create a tenant and return its first admin key. The endpoint is operator-only
    and deliberately absent from the SDKs."""
    response = httpx.post(
        f"{base_url}/v1/admin/organizations",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": name, "slug": slug, "key_name": "onboarding"},
        timeout=30,
    )
    if response.status_code == httpx.codes.CONFLICT:
        sys.exit(f"Organization slug {slug!r} is taken; pass --slug or use CORTEX_API_KEY.")
    response.raise_for_status()
    body = response.json()
    print(f"organization {body['organization']['name']} ({body['organization']['id']})")
    secret: str = body["api_key"]["secret"]
    return secret


def ingest(workload: Cortex) -> list[str]:
    ids = []
    for path in sorted(DOCUMENTS.glob("*.md")):
        try:
            document = workload.documents.upload(path, metadata={"collection": "policies"})
            print(f"indexed {path.name}: {document.chunk_count} chunks")
            ids.append(document.id)
        except ConflictError as error:
            print(f"already indexed {path.name} ({error.document_id})")
    return ids


def answer(workload: Cortex) -> str:
    completion = workload.chat.complete(
        objective="quality",
        messages=[
            {
                "role": "system",
                "content": "Answer from company policy only. Cite sources as [n].",
            },
            {"role": "user", "content": QUESTION},
        ],
        knowledge={"top_k": 5, "min_confidence": 0.2},
        metadata={"feature": "policy-assistant"},
    )
    print(f"Q: {QUESTION}\nA: {completion.output}")
    if completion.knowledge:
        confidence = completion.knowledge.confidence
        print(f"retrieval confidence: {confidence.level} ({confidence.score:.2f})")
        for citation in completion.knowledge.citations:
            marker = "*" if citation.cited else " "
            print(f" {marker}[{citation.index}] {citation.label}")
    return completion.id


def audit(workload: Cortex, completion_id: str) -> None:
    cost_by_provider: dict[str, float] = defaultdict(float)
    for execution in workload.router.iter_executions(completion_id=completion_id):
        outcome = "ok" if execution.success else f"failed ({execution.error_type})"
        print(
            f"attempt {execution.attempt}: {execution.provider}/{execution.model} "
            f"{execution.latency_ms:.0f} ms, "
            f"{execution.prompt_tokens}+{execution.completion_tokens} tokens, "
            f"${execution.cost_estimate:.6f}, {outcome}"
        )
        cost_by_provider[execution.provider] += execution.cost_estimate
    for provider, cost in sorted(cost_by_provider.items()):
        print(f"total {provider}: ${cost:.6f}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", default="Northwind")
    parser.add_argument("--slug", default="northwind")
    parser.add_argument(
        "--keep", action="store_true", help="keep the documents and workload key afterwards"
    )
    args = parser.parse_args()

    base_url = os.environ.get("CORTEX_BASE_URL", "http://localhost:8000").rstrip("/")
    admin_token = os.environ.get("CORTEX_ADMIN_TOKEN")
    admin_key = os.environ.get("CORTEX_API_KEY")

    step("1. Organization")
    if admin_token:
        admin_key = bootstrap_organization(base_url, admin_token, args.name, args.slug)
    elif not admin_key:
        sys.exit("Set CORTEX_ADMIN_TOKEN to create an organization, or CORTEX_API_KEY.")
    else:
        print("using the organization of CORTEX_API_KEY")

    with Cortex(api_key=admin_key, base_url=base_url) as admin:
        organization = admin.system.organization()
        print(f"acting as admin of {organization.name} ({organization.slug})")

        step("2. Scoped workload key")
        issued = admin.api_keys.create(name="policy-assistant", role="member", expires_in_days=90)
        print(f"member key {issued.prefix}... expires {issued.expires_at:%Y-%m-%d}")

        document_ids: list[str] = []
        try:
            with Cortex(api_key=issued.secret, base_url=base_url) as workload:
                step("3. Ingest documents")
                document_ids = ingest(workload)

                step("4. Grounded answer")
                completion_id = answer(workload)

                step("5. Audit")
                audit(workload, completion_id)

            step("6. Rotate and revoke")
            rotated = admin.api_keys.rotate(issued.id, grace_period_seconds=300)
            print(f"rotated to {rotated.prefix}...; the old key works for 5 more minutes")
            if not args.keep:
                admin.api_keys.revoke(rotated.id)
                admin.api_keys.revoke(issued.id)
                print("revoked both keys")
        except CortexError as error:
            print(f"failed: {error}", file=sys.stderr)
            admin.api_keys.revoke(issued.id)
            return 1
        finally:
            if not args.keep:
                for document_id in document_ids:
                    admin.documents.delete(document_id)

    print("\ndone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
