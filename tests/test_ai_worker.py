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

import ai_worker  # noqa: E402


class AiWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ff_ai_"))
        self.pending_dir = self.tmp / "pending"
        self.pending_dir.mkdir()
        (self.pending_dir / "神秘文件.docx").write_text("x", encoding="utf-8")
        self.dest = self.tmp / "dest"
        self.zones = {
            "dest_roots": {
                "pending": str(self.pending_dir).replace("\\", "/"),
                "training": str(self.dest).replace("\\", "/"),
            }
        }
        self.ff = self.tmp / "ff"
        (self.ff / "state").mkdir(parents=True)
        (self.ff / "runs").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_drain_moves_when_llm_confident(self):
        def fake_llm(prompt: str):
            return {
                "results": [
                    {
                        "name": "神秘文件.docx",
                        "dest_key": "training",
                        "reason": "培训材料",
                        "confidence": 0.95,
                    }
                ]
            }

        result = ai_worker.drain_pending(
            self.ff,
            limit=10,
            llm=fake_llm,
            zones=self.zones,
            pending_dir=self.pending_dir,
        )
        self.assertEqual(result["moved"], 1)
        self.assertTrue((self.dest / "神秘文件.docx").exists())

    def test_rejects_dest_not_in_whitelist(self):
        def fake_llm(prompt: str):
            return {
                "results": [
                    {"name": "神秘文件.docx", "dest_key": "nowhere", "reason": "bad", "confidence": 0.99}
                ]
            }

        result = ai_worker.drain_pending(
            self.ff,
            limit=10,
            llm=fake_llm,
            zones=self.zones,
            pending_dir=self.pending_dir,
        )
        self.assertEqual(result["moved"], 0)
        self.assertTrue((self.pending_dir / "神秘文件.docx").exists())

    def test_low_confidence_stays(self):
        def fake_llm(prompt: str):
            return {
                "results": [
                    {"name": "神秘文件.docx", "dest_key": "training", "reason": "?", "confidence": 0.5}
                ]
            }

        result = ai_worker.drain_pending(
            self.ff,
            limit=10,
            llm=fake_llm,
            zones=self.zones,
            pending_dir=self.pending_dir,
        )
        self.assertEqual(result["unresolved"], 1)


if __name__ == "__main__":
    unittest.main()
