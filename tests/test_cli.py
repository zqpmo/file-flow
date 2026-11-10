# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

FF = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FF))

import cli  # noqa: E402


class CliTests(unittest.TestCase):
    def test_dry_run_flag(self):
        tmp = Path(tempfile.mkdtemp(prefix="ff_cli_"))
        try:
            ff = tmp / "ff"
            ff.mkdir()
            (ff / "runs").mkdir()
            (ff / "state").mkdir()
            src = tmp / "src"
            src.mkdir()
            (src / "HANDBOOK_z.md").write_text("z", encoding="utf-8")
            zones = {
                "sources": {"extra": str(src).replace("\\", "/")},
                "dest_roots": {
                    "knowledge": str(tmp / "d").replace("\\", "/"),
                    "pending": str(tmp / "p").replace("\\", "/"),
                },
                "exempt_name_prefixes": [],
                "exempt_dirs": [],
            }
            zones_p = ff / "zones.json"
            zones_p.write_text(json.dumps(zones), encoding="utf-8")
            rules_p = ff / "rules.json"
            rules_p.write_text(
                json.dumps(
                    [
                        {
                            "id": "h",
                            "priority": 1,
                            "match": {"name_any": ["HANDBOOK"]},
                            "destination": "knowledge",
                            "confidence": 0.9,
                        },
                        {
                            "id": "d",
                            "priority": 0,
                            "match": {},
                            "destination": "pending",
                            "confidence": 0.3,
                        },
                    ]
                ),
                encoding="utf-8",
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                # Default is dry-run — no --execute needed
                code = cli.main(
                    [
                        "--ff-root",
                        str(ff),
                        "--zones",
                        str(zones_p),
                        "--rules",
                        str(rules_p),
                    ]
                )
            self.assertEqual(code, 0)
            out = buf.getvalue()
            self.assertIn("would_move", out)
            self.assertIn('"dry_run": true', out)
            # file must not have moved
            self.assertTrue((src / "HANDBOOK_z.md").exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
