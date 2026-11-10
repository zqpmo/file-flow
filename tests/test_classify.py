# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

FF = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FF))

import engine  # noqa: E402


def _rules():
    return json.loads((FF / "rules.json").read_text(encoding="utf-8"))


def _zones():
    return json.loads((FF / "zones.json").read_text(encoding="utf-8"))


class ClassifyTests(unittest.TestCase):
    def test_lockfile_ignored(self):
        r = engine.classify(Path("C:/Users/x/Downloads/~$foo.docx"), _rules(), _zones())
        self.assertEqual(r["decision"], "ignore")

    def test_handbook_auto(self):
        r = engine.classify(Path("C:/Users/x/HANDBOOK_Unit_1.md"), _rules(), _zones())
        self.assertEqual(r["decision"], "auto")
        self.assertEqual(r["dest_key"], "knowledge")

    def test_project_docs_goes_projects(self):
        r = engine.classify(
            Path("C:/Users/x/Desktop/ProjectX-status.docx"), _rules(), _zones()
        )
        self.assertEqual(r["decision"], "auto")
        self.assertEqual(r["dest_key"], "projects")

    def test_priority_lockfile_beats_project_docs(self):
        r = engine.classify(
            Path("C:/Users/x/Downloads/~$ProjectX-notes.docx"), _rules(), _zones()
        )
        self.assertEqual(r["decision"], "ignore")

    def test_default_pending_when_unmatched_high_conf(self):
        r = engine.classify(Path("C:/Users/x/Downloads/random.xyz"), _rules(), _zones())
        self.assertEqual(r["decision"], "pending_ai")
        self.assertEqual(r["dest_key"], "pending")

    def test_dest_key_always_in_whitelist(self):
        zones = _zones()
        for name in ["a.docx", "HANDBOOK_x.md", "ProjectX.pptx", "unknown.bin"]:
            r = engine.classify(Path("C:/Users/x/") / name, _rules(), zones)
            self.assertIn(r["dest_key"], zones["dest_roots"])


if __name__ == "__main__":
    unittest.main()
