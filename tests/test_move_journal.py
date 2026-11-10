# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

FF = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FF))

import engine  # noqa: E402


class MoveJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ff_test_"))
        self.src = self.tmp / "src"
        self.src.mkdir()
        self.ff = self.tmp / "ff"
        self.ff.mkdir()
        (self.ff / "runs").mkdir()
        (self.ff / "state").mkdir()
        self.zones = {
            "dest_roots": {
                "knowledge": str(self.tmp / "dest_kb").replace("\\", "/"),
                "pending": str(self.tmp / "dest_pending").replace("\\", "/"),
            },
            "exempt_name_prefixes": ["~$"],
            "exempt_dirs": [],
            "home_root_max_depth": 0,
        }
        self.zones_path = self.ff / "zones.json"
        self.zones_path.write_text(
            json.dumps(self.zones, ensure_ascii=False), encoding="utf-8"
        )
        self.rules = [
            {
                "id": "handbook",
                "priority": 10,
                "match": {"name_any": ["HANDBOOK"]},
                "destination": "knowledge",
                "confidence": 0.9,
            },
            {
                "id": "default",
                "priority": 0,
                "match": {},
                "destination": "pending",
                "confidence": 0.3,
            },
        ]
        self.rules_path = self.ff / "rules.json"
        self.rules_path.write_text(
            json.dumps(self.rules, ensure_ascii=False), encoding="utf-8"
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_run_once_moves_and_journals(self):
        f = self.src / "HANDBOOK_Unit_1.md"
        f.write_text("hello", encoding="utf-8")
        report = engine.run_once(
            self.ff,
            zones_path=self.zones_path,
            rules_path=self.rules_path,
            dry_run=False,
            source_filter=None,
            extra_sources=[str(self.src)],
        )
        self.assertEqual(report["moved"], 1)
        dest = Path(self.zones["dest_roots"]["knowledge"]) / "HANDBOOK_Unit_1.md"
        self.assertTrue(dest.exists())
        self.assertFalse(f.exists())
        journal = self.ff / "runs" / report["run_id"] / "journal.ndjson"
        lines = journal.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(rec["action"], "move")

    def test_dry_run_does_not_move(self):
        f = self.src / "HANDBOOK_x.md"
        f.write_text("x", encoding="utf-8")
        report = engine.run_once(
            self.ff,
            zones_path=self.zones_path,
            rules_path=self.rules_path,
            dry_run=True,
            source_filter=None,
            extra_sources=[str(self.src)],
        )
        self.assertEqual(report["would_move"], 1)
        self.assertEqual(report["moved"], 0)
        self.assertTrue(f.exists())

    def test_rollback_restores(self):
        f = self.src / "HANDBOOK_y.md"
        f.write_text("y", encoding="utf-8")
        report = engine.run_once(
            self.ff,
            zones_path=self.zones_path,
            rules_path=self.rules_path,
            dry_run=False,
            source_filter=None,
            extra_sources=[str(self.src)],
        )
        result = engine.rollback_run(self.ff, report["run_id"])
        self.assertEqual(result["restored"], 1)
        self.assertTrue(f.exists())

    def test_rollback_refuses_occupied_original_path(self):
        f = self.src / "HANDBOOK_z.md"
        f.write_text("orig", encoding="utf-8")
        report = engine.run_once(
            self.ff,
            zones_path=self.zones_path,
            rules_path=self.rules_path,
            dry_run=False,
            source_filter=None,
            extra_sources=[str(self.src)],
        )
        self.assertEqual(report["moved"], 1)
        # User creates a new file at the original path before rollback
        f.write_text("newer", encoding="utf-8")
        result = engine.rollback_run(self.ff, report["run_id"])
        self.assertEqual(result["restored"], 0)
        self.assertTrue(any(
            s.get("reason") == "original_path_occupied" for s in result.get("skipped", [])
        ))
        # New file must not be overwritten
        self.assertEqual(f.read_text(encoding="utf-8"), "newer")

    def test_first_run_creates_state_dir(self):
        # ff has no state/ pre-created beyond setUp — remove it to simulate first run
        import shutil as _sh
        state = self.ff / "state"
        if state.exists():
            _sh.rmtree(state)
        f = self.src / "HANDBOOK_fresh.md"
        f.write_text("fresh", encoding="utf-8")
        report = engine.run_once(
            self.ff,
            zones_path=self.zones_path,
            rules_path=self.rules_path,
            dry_run=False,
            source_filter=None,
            extra_sources=[str(self.src)],
        )
        self.assertEqual(report["moved"], 1)
        self.assertTrue((self.ff / "state" / "seen_hashes.json").exists())

    def test_exempt_dirs_skipped(self):
        exempt_root = self.src / "AppData"
        exempt_root.mkdir(parents=True, exist_ok=True)
        (exempt_root / "hidden.md").write_text("h", encoding="utf-8")
        (self.src / "HANDBOOK_visible.md").write_text("v", encoding="utf-8")
        zones = dict(self.zones)
        zones["exempt_dirs"] = ["AppData"]
        zones_path = self.ff / "zones2.json"
        zones_path.write_text(json.dumps(zones, ensure_ascii=False), encoding="utf-8")
        report = engine.run_once(
            self.ff,
            zones_path=zones_path,
            rules_path=self.rules_path,
            dry_run=False,
            source_filter=None,
            extra_sources=[str(self.src)],
        )
        # only visible handbook moves; AppData file stays
        self.assertEqual(report["moved"], 1)
        self.assertTrue((exempt_root / "hidden.md").exists())
        self.assertFalse((self.src / "HANDBOOK_visible.md").exists())


if __name__ == "__main__":
    unittest.main()
