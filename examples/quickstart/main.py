"""The smallest useful Cortex program, in Python.

Checks the API, lists what can be routed to, and sends one completion.
Reads CORTEX_API_KEY and CORTEX_BASE_URL.

    uv run python examples/quickstart/main.py
"""

import sys

from cortex import Cortex, CortexError


def main() -> int:
    with Cortex() as cortex:
        readiness = cortex.system.readiness()
        print(f"Cortex {readiness.version} is {readiness.status}")

        routable = [m for m in cortex.router.models().models if m.available and m.allowed]
        if not routable:
            print(
                "No models are routable. Configure a provider key on the API, e.g. OPENAI_API_KEY.",
                file=sys.stderr,
            )
            return 1
        print("Routable models:", ", ".join(m.id for m in routable))

        try:
            completion = cortex.chat.complete(
                objective="balanced",
                messages=[
                    {"role": "system", "content": "Answer in one short paragraph."},
                    {
                        "role": "user",
                        "content": "What does an intelligence infrastructure layer do?",
                    },
                ],
            )
        except CortexError as error:
            print(f"Cortex rejected the request: {error}", file=sys.stderr)
            return 1

    print(f"\n{completion.output}\n")
    print(
        " | ".join(
            [
                f"{completion.provider}/{completion.model}",
                completion.routing_reason,
                f"{completion.tokens.total} tokens",
                f"{completion.latency_ms:.0f} ms",
                f"${completion.cost_estimate:.6f}",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
