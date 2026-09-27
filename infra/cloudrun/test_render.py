"""Tests for render.py against the real manifests and policies. Standard library only:

python3 infra/cloudrun/test_render.py
"""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import render

CLOUDRUN = Path(__file__).parent
POLICIES = CLOUDRUN.parent / "monitoring" / "gcp" / "policies"

MANIFEST_VARS = {
    "SERVICE_NAME": "cortex-api-staging",
    "JOB_NAME": "cortex-migrate-staging",
    "ENVIRONMENT": "staging",
    "RELEASE_LABEL": "1-0-0-alpha",
    "IMAGE": "us-central1-docker.pkg.dev/p/cortex/cortex-api:0123abc",
    "MIN_INSTANCES": "0",
    "MAX_INSTANCES": "5",
    "API_CPU": "1",
    "API_MEMORY": "1Gi",
    "CLOUDSQL_CONNECTION": "p:us-central1:cortex-staging",
    "VPC_NETWORK": "default",
    "VPC_SUBNET": "default",
    "RUNTIME_SERVICE_ACCOUNT": "cortex-api-staging@p.iam.gserviceaccount.com",
    "COLLECTOR_IMAGE": "otel/opentelemetry-collector-contrib:0.120.0",
    "COLLECTOR_CONFIG_SECRET": "cortex-staging-otel-collector-config",
}
POLICY_VARS = {
    "ENVIRONMENT": "production",
    "SERVICE_NAME": "cortex-api-production",
    "API_HOST": "api.cortex.celestra.ai",
    "UPTIME_CHECK_ID": "cortex-api-production-health-abc",
}


def env_names(rendered: str) -> list[str]:
    return re.findall(r"^\s*- name: (CORTEX_[A-Z0-9_]+)$", rendered, re.MULTILINE)


class RenderManifestsTest(unittest.TestCase):
    def render(self, template: str, env: str, secrets: dict[str, str] | None = None) -> str:
        values = render.parse_env_file(CLOUDRUN / f"{env}.env.yaml")
        values["CORTEX_RELEASE"] = "1.0.0-alpha"
        return render.render(
            (CLOUDRUN / template).read_text(), values, secrets or {}, MANIFEST_VARS
        )

    def test_service_renders_every_environment(self) -> None:
        for env in ("staging", "production"):
            with self.subTest(env=env):
                rendered = self.render(
                    "service.yaml", env, {"CORTEX_DATABASE_URL": f"cortex-{env}-database-url"}
                )
                self.assertNotIn("${", rendered)
                self.assertNotIn("{{ENV}}", rendered)
                names = env_names(rendered)
                self.assertIn("CORTEX_ENV", names)
                api_container = rendered.split("- name: otel-collector", 1)[0]
                self.assertEqual(env_names(api_container)[-1], "CORTEX_DATABASE_URL")
                self.assertIn(f'value: "{env}"', rendered)
                self.assertIn(f"name: cortex-{env}-database-url\n", rendered)

    def test_env_entries_are_indented_under_the_api_container(self) -> None:
        rendered = self.render("service.yaml", "staging")
        block = rendered.split("          env:\n", 1)[1]
        self.assertTrue(block.startswith("            - name: CORTEX_ENV\n"))
        self.assertIn('              value: "staging"\n', block)

    def test_migrate_job_renders(self) -> None:
        rendered = self.render("migrate-job.yaml", "production")
        self.assertIn("- migrate", rendered)
        self.assertIn('value: "production"', rendered)
        self.assertNotIn("${", rendered)

    def test_production_env_is_safe(self) -> None:
        values = render.parse_env_file(CLOUDRUN / "production.env.yaml")
        origins = values["CORTEX_API_CORS_ORIGINS"].split(",")
        self.assertTrue(all(o.startswith("https://") for o in origins))
        self.assertEqual(values["CORTEX_AUTH_REQUIRE_API_KEY"], "true")
        self.assertEqual(values["CORTEX_ENV"], "production")

    def test_values_are_json_quoted(self) -> None:
        value = 'a "b": c'
        out = render.env_entries({"CORTEX_X": value}, {}, "")
        self.assertIn(f"value: {json.dumps(value)}", out)

    def test_missing_variables_are_reported(self) -> None:
        with self.assertRaisesRegex(render.RenderError, "IMAGE"):
            render.render("image: ${IMAGE}\n", {}, {}, {})

    def test_reserved_and_duplicate_names_are_rejected(self) -> None:
        with self.assertRaisesRegex(render.RenderError, "PORT"):
            render.env_entries({"PORT": "8080"}, {}, "")
        with self.assertRaisesRegex(render.RenderError, "both"):
            render.env_entries({"CORTEX_A": "1"}, {"CORTEX_A": "s"}, "")

    def test_values_without_an_env_marker_are_rejected(self) -> None:
        with self.assertRaisesRegex(render.RenderError, "no \\{\\{ENV\\}\\}"):
            render.render("kind: Service\n", {"CORTEX_A": "1"}, {}, {})

    def test_env_file_parsing(self) -> None:
        path = CLOUDRUN / "__test.env.yaml"
        path.write_text("# comment\nA: plain\nB: \"quoted: yes\"\nC: 'single'\n")
        try:
            self.assertEqual(
                render.parse_env_file(path), {"A": "plain", "B": "quoted: yes", "C": "single"}
            )
            path.write_text("not a mapping\n")
            with self.assertRaises(render.RenderError):
                render.parse_env_file(path)
        finally:
            path.unlink()


class RenderPoliciesTest(unittest.TestCase):
    def test_policies_render(self) -> None:
        policies = sorted(POLICIES.glob("*.yaml"))
        self.assertGreaterEqual(len(policies), 4)
        for policy in policies:
            with self.subTest(policy=policy.name):
                rendered = render.render(policy.read_text(), {}, {}, POLICY_VARS)
                self.assertNotIn("${", rendered)
                self.assertRegex(
                    rendered, re.compile(r'^displayName: "Cortex API production: .+"$', re.M)
                )


if __name__ == "__main__":
    unittest.main(verbosity=1)
