# -*- coding: utf-8 -*-
"""AI Worker：消化 inbox-pending，落点必须在 zones 白名单内。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import engine

FF_ROOT = Path(__file__).resolve().parent


def _default_llm(prompt: str) -> dict:
    cmd = os.environ.get("FF_LLM_CMD")
    if not cmd:
        bridge = FF_ROOT / "llm_classify.py"
        if bridge.is_file():
            cmd = f'"{sys.executable}" "{bridge}"'
        else:
            raise RuntimeError("FF_LLM_CMD not configured")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    proc = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        shell=True,
        timeout=180,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError(f"llm cmd failed rc={proc.returncode}: {proc.stderr[:300]}")
    out = proc.stdout.strip()
    if not out:
        raise RuntimeError(f"llm cmd empty stdout: {proc.stderr[:300]}")
    data = json.loads(out)
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data


def _build_prompt(files: list[dict], dest_keys: list[str]) -> str:
    return json.dumps(
        {
            "task": "classify_files_into_dest",
            "dest_whitelist": dest_keys,
            "files": files,
            "output_schema": {
                "results": [
                    {"name": "str", "dest_key": "str", "reason": "str", "confidence": "float_0_to_1"}
                ]
            },
            "instruction": "对每个文件输出 results 数组，dest_key 必须在 dest_whitelist 内。",
        },
        ensure_ascii=False,
        indent=2,
    )


def drain_pending(
    ff_root: Path,
    *,
    limit: int = 50,
    llm=None,
    zones: dict | None = None,
    pending_dir: Path | None = None,
) -> dict:
    ff_root = Path(ff_root)
    zones = zones or engine.load_zones(FF_ROOT / "zones.json")
    pending_dir = Path(pending_dir or zones["dest_roots"]["pending"])
    llm = llm or _default_llm
    dest_roots = {k: Path(v) for k, v in zones["dest_roots"].items()}
    if not pending_dir.is_dir():
        return {"moved": 0, "unresolved": 0, "considered": 0}
    files = sorted([p for p in pending_dir.iterdir() if p.is_file()])[:limit]
    decisions_log = ff_root / "runs" / "ai-decisions.jsonl"
    decisions_log.parent.mkdir(parents=True, exist_ok=True)
    moved = unresolved = 0
    if not files:
        return {"moved": 0, "unresolved": 0, "considered": 0}
    prompt = _build_prompt(
        [{"name": p.name, "path": str(p), "size": p.stat().st_size} for p in files],
        list(zones["dest_roots"].keys()),
    )
    try:
        raw = llm(prompt)
    except Exception as e:  # AI 失败不阻塞
        return {
            "moved": 0,
            "unresolved": len(files),
            "considered": len(files),
            "error": str(e),
        }
    results = raw.get("results") if isinstance(raw, dict) else None
    if results is None and isinstance(raw, dict) and "dest_key" in raw:
        results = [raw]
    results = results or []
    by_name = {
        r.get("name") or Path(str(r.get("path", ""))).name: r for r in results
    }
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-ai"
    with open(decisions_log, "a", encoding="utf-8") as logf:
        for src in files:
            r = by_name.get(src.name) or {}
            dest_key = r.get("dest_key")
            conf = float(r.get("confidence") or 0)
            rec = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "run_id": run_id,
                "file": src.name,
                "dest_key": dest_key,
                "confidence": conf,
                "reason": r.get("reason", ""),
            }
            if dest_key in dest_roots and conf >= 0.8:
                final = engine._unique_dest(dest_roots[dest_key] / src.name)
                final.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(final))
                rec["action"] = "move"
                rec["dest"] = str(final)
                moved += 1
            else:
                rec["action"] = "unresolved"
                unresolved += 1
            logf.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {
        "moved": moved,
        "unresolved": unresolved,
        "considered": len(files),
        "run_id": run_id,
    }


if __name__ == "__main__":
    report = drain_pending(FF_ROOT, limit=50)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0)
