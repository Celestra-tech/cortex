#!/usr/bin/env python3
"""Render a Cloud Run manifest template. Standard library only, so it runs anywhere.

    render.py TEMPLATE --env-file staging.env.yaml \
        --set CORTEX_RELEASE=1.0.0-alpha \
        --secret CORTEX_DATABASE_URL=cortex-staging-database-url > service.rendered.yaml

- `${NAME}` is replaced from the process environment; any unset name is an error.
- The `# {{ENV}}` line becomes the container's `env:` entries: the env file (a flat
  `KEY: value` mapping), then `--set` values, then `--secret` references to the
  latest version of each Secret Manager secret.
- Templates without that line (alert policies) only get `${NAME}` substitution.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

PLACEHOLDER = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
# A YAML comment, so templates stay valid YAML and formatters leave the marker alone.
ENV_MARKER = re.compile(r"^(?P<indent>[ ]*)# \{\{ENV\}\}[ ]*$", re.MULTILINE)
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
RESERVED = frozenset({"PORT", "K_SERVICE", "K_REVISION", "K_CONFIGURATION"})


class RenderError(Exception):
    pass


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if not sep or not ENV_NAME.match(key):
            raise RenderError(f"{path}:{number}: expected `KEY: value`, got {raw!r}")
        if value.startswith('"'):
            value = json.loads(value)
        elif value.startswith("'") and value.endswith("'") and len(value) >= 2:
            value = value[1:-1].replace("''", "'")
        values[key] = value
    return values


def pairs(items: list[str], flag: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not ENV_NAME.match(key) or not value:
            raise RenderError(f"{flag} expects NAME=VALUE, got {item!r}")
        result[key] = value
    return result


def env_entries(values: dict[str, str], secrets: dict[str, str], indent: str) -> str:
    clash = (set(values) | set(secrets)) & RESERVED
    if clash:
        raise RenderError(f"Cloud Run sets these itself: {sorted(clash)}")
    both = set(values) & set(secrets)
    if both:
        raise RenderError(f"set both as a value and a secret: {sorted(both)}")
    lines: list[str] = []
    for name, value in values.items():
        lines += [f"- name: {name}", f"  value: {json.dumps(value)}"]
    for name, secret in secrets.items():
        lines += [
            f"- name: {name}",
            "  valueFrom:",
            "    secretKeyRef:",
            f"      name: {secret}",
            "      key: latest",
        ]
    return "\n".join(indent + line for line in lines) if lines else f"{indent}[]"


def render(
    template: str, values: dict[str, str], secrets: dict[str, str], environ: dict[str, str]
) -> str:
    missing = sorted({name for name in PLACEHOLDER.findall(template) if not environ.get(name)})
    if missing:
        raise RenderError(f"unset variables: {', '.join(missing)}")
    substituted = PLACEHOLDER.sub(lambda m: environ[m.group(1)], template)
    marker = ENV_MARKER.search(substituted)
    if marker is None:
        if values or secrets:
            raise RenderError("template has no {{ENV}} line for the given values")
        return substituted
    entries = env_entries(values, secrets, marker.group("indent"))
    return substituted[: marker.start()] + entries + substituted[marker.end() :]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("template", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("--secret", action="append", default=[], metavar="NAME=SECRET_ID")
    args = parser.parse_args(argv)
    try:
        from_file = parse_env_file(args.env_file) if args.env_file else {}
        values = {**from_file, **pairs(args.set, "--set")}
        secrets = pairs(args.secret, "--secret")
        sys.stdout.write(render(args.template.read_text(), values, secrets, dict(os.environ)))
    except (RenderError, OSError, json.JSONDecodeError) as exc:
        print(f"render.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
