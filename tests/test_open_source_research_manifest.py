"""Validate the upstream research inventory without network access."""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "research" / "open_source_research_manifest.json"


class OpenSourceResearchManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_every_project_has_provenance_and_a_licence(self):
        projects = self.manifest["projects"]
        self.assertGreaterEqual(len(projects), 7)
        names = set()
        for project in projects:
            with self.subTest(project=project.get("name")):
                self.assertTrue(project["name"].strip())
                self.assertTrue(project["repository"].startswith("https://github.com/"))
                self.assertTrue(project["license"].strip())
                self.assertTrue(project["role"].strip())
                self.assertTrue(project["planned_use"].strip())
                self.assertIn(project["integration_status"], {
                    "reference_only", "design_reference", "not_installed",
                    "already_used_research_only",
                })
                self.assertNotIn(project["name"], names)
                names.add(project["name"])

    def test_evaluation_rules_include_core_anti_overfit_safeguards(self):
        rules = " ".join(self.manifest["evaluation_rules"]).lower()
        for phrase in (
            "same time range", "slippage", "walk-forward",
            "fully matured", "untouched test period", "paper-trade",
            "never auto-promote",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, rules)

    def test_live_execution_and_leverage_are_explicitly_out_of_scope(self):
        rules = " ".join(self.manifest["evaluation_rules"]).lower()
        self.assertIn("paper-only", rules)
        self.assertIn("no live broker integration or leverage", rules)


if __name__ == "__main__":
    unittest.main()
