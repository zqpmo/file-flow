# -*- coding: utf-8 -*-
"""File-Flow 引擎：scan / classify / move / journal。零第三方依赖。"""
from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path


def load_rules(path: Path) -> list[dict]:
    rules = json.loads(Path(path).read_text(encoding="utf-8"))
    return sorted(rules, key=lambda r: -int(r.get("priority", 0)))


def load_zones(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _match(path: Path, match: dict) -> bool:
    name = path.name
    if not match:
        return True
    prefixes = match.get("name_prefixes") or []
    if prefixes and not any(name.startswith(p) for p in prefixes):
        return False
    any_words = match.get("name_any") or []
    if any_words and not any(w in name for w in any_words):
        return False
    exts = [e.lower() for e in (match.get("extensions") or [])]
    if exts:
        full = name.lower()
        if not any(full.endswith(e) for e in exts):
            return False
    return True


def classify(path: Path, rules: list[dict], zones: dict) -> dict:
    """按 priority 降序命中第一条规则；返回 decision/dest_key/confidence/rule_id。"""
    path = Path(path)
    for rule in rules:
        if _match(path, rule.get("match") or {}):
            decision = rule.get("decision")
            dest_key = rule.get("destination")
            confidence = float(rule.get("confidence", 0.0))
            if decision is None:
                if confidence >= 0.85:
                    decision = "auto"
                elif confidence >= 0.4:
                    decision = "pending_ai"
                else:
                    decision = "pending_ai"
            if dest_key is not None and dest_key not in zones["dest_roots"]:
                raise ValueError(f"rule {rule['id']} dest not in whitelist: {dest_key}")
            return {
                "decision": decision,
                "rule_id": rule["id"],
                "dest_key": dest_key,
                "confidence": confidence,
            }
    raise RuntimeError("rules must contain a catch-all default")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_exempt(path: Path, zones: dict) -> bool:
    name = path.name
    for p in zones.get("exempt_name_prefixes") or []:
        if name.startswith(p):
            return True
    if name.lower() == "desktop.ini":
        return True
    return False


def _unique_dest(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    for i in range(2, 1000):
        cand = dest.with_name(f"{stem}_{i}{suffix}")
        if not cand.exists():
            return cand
    raise RuntimeError(f"cannot uniquify dest: {dest}")


def _iter_sources(zones: dict, source_filter: list[str] | None, extra_sources: list[str] | None):
    for key, uri in (zones.get("sources") or {}).items():
        if source_filter and key not in source_filter:
            continue
        p = Path(uri)
        if p.is_dir():
            yield key, p
    for uri in extra_sources or []:
        p = Path(uri)
        if p.is_dir():
            yield "extra", p


def _iter_files(root: Path, max_depth: int | None):
    root = Path(root)
    if max_depth == 0:
        for p in root.iterdir():
            if p.is_file():
                yield p
        return
    for p in root.rglob("*"):
        if p.is_file():
            yield p


def run_once(
    ff_root: Path,
    *,
    zones_path: Path,
    rules_path: Path,
    dry_run: bool,
    source_filter: list[str] | None = None,
    extra_sources: list[str] | None = None,
) -> dict:
    ff_root = Path(ff_root)
    zones = load_zones(zones_path)
    rules = load_rules(rules_path)
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    )
    run_dir = ff_root / "runs" / run_id
    journal_path = run_dir / "journal.ndjson"
    report_path = run_dir / "run-report.json"
    state_path = ff_root / "state" / "seen_hashes.json"
    seen: dict = {}
    if state_path.exists():
        seen = json.loads(state_path.read_text(encoding="utf-8"))

    moves: list[dict] = []
    pending: list[dict] = []
    ignored: list[dict] = []
    dest_roots = {k: Path(v) for k, v in zones["dest_roots"].items()}

    for zone_key, zone_path in _iter_sources(zones, source_filter, extra_sources):
        max_depth = 0 if zone_key == "home_root" else None
        for src in list(_iter_files(zone_path, max_depth)):
            if _is_exempt(src, zones):
                ignored.append({"src": str(src), "reason": "exempt"})
                continue
            try:
                digest = _sha256(src)
            except OSError:
                continue
            if seen.get(digest) == "done":
                ignored.append({"src": str(src), "reason": "seen"})
                continue
            verdict = classify(src, rules, zones)
            dest_key = verdict["dest_key"]
            sub = ""
            for rule in rules:
                if rule["id"] == verdict["rule_id"]:
                    sub = rule.get("subfolder") or ""
                    break
            if verdict["decision"] == "ignore":
                ignored.append({"src": str(src), "rule": verdict["rule_id"]})
                seen[digest] = "ignored"
                continue
            if verdict["decision"] == "auto":
                target_dir = dest_roots[dest_key]
                if sub:
                    target_dir = target_dir / sub
                moves.append(
                    {
                        "src": str(src),
                        "dest": str(target_dir / src.name),
                        "dest_key": dest_key,
                        "rule_id": verdict["rule_id"],
                        "confidence": verdict["confidence"],
                        "decision": verdict["decision"],
                    }
                )
            else:
                # pending：物理落入 inbox-pending 队列，建议 dest 仅记录
                pending_dir = dest_roots.get("pending")
                queue_dest = (pending_dir / src.name) if pending_dir else (dest_roots[dest_key] / src.name)
                pending.append(
                    {
                        "src": str(src),
                        "dest": str(queue_dest),
                        "suggested_dest": str(dest_roots[dest_key] / src.name),
                        "dest_key": dest_key,
                        "rule_id": verdict["rule_id"],
                        "confidence": verdict["confidence"],
                        "decision": verdict["decision"],
                    }
                )

    if not dry_run:
        run_dir.mkdir(parents=True, exist_ok=True)
        skipped: list[dict] = []
        with open(journal_path, "a", encoding="utf-8") as jf:
            for e in moves:
                try:
                    final = _unique_dest(Path(e["dest"]))
                    final.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(e["src"], final)
                except OSError as ex:
                    skipped.append({"src": e["src"], "error": str(ex)})
                    continue
                rec = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "run_id": run_id,
                    "src": e["src"],
                    "dest": str(final),
                    "sha256": _sha256(final),
                    "rule_id": e["rule_id"],
                    "action": "move",
                }
                jf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                seen[rec["sha256"]] = "done"
            for e in pending:
                try:
                    final = _unique_dest(Path(e["dest"]))
                    final.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(e["src"], final)
                except OSError as ex:
                    skipped.append({"src": e["src"], "error": str(ex)})
                    continue
                e["dest"] = str(final)
                rec = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "run_id": run_id,
                    "src": e["src"],
                    "dest": str(final),
                    "sha256": _sha256(final),
                    "rule_id": e["rule_id"],
                    "action": "to_pending",
                }
                jf.write(json.dumps(rec, ensure_ascii=False) + "\n")
        state_path.write_text(
            json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if skipped:
            (run_dir / "skipped.json").write_text(
                json.dumps(skipped, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    report = {
        "run_id": run_id,
        "dry_run": dry_run,
        "ts": datetime.now(timezone.utc).isoformat(),
        "moved": 0 if dry_run else len(moves),
        "would_move": len(moves),
        "pending_ai": len(pending),
        "ignored": len(ignored),
        "moves": moves,
        "pending": pending,
    }
    if not dry_run:
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    latest = ff_root / "runs" / "latest-report.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def rollback_run(ff_root: Path, run_id: str) -> dict:
    ff_root = Path(ff_root)
    journal_path = ff_root / "runs" / run_id / "journal.ndjson"
    if not journal_path.exists():
        raise FileNotFoundError(journal_path)
    restored = 0
    text = journal_path.read_text(encoding="utf-8")
    lines = [json.loads(x) for x in text.splitlines() if x.strip()]
    with open(journal_path, "a", encoding="utf-8") as jf:
        for rec in reversed(lines):
            if rec.get("action") not in ("move", "to_pending"):
                continue
            dest, src = Path(rec["dest"]), Path(rec["src"])
            if dest.exists():
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dest), str(src))
                jf.write(
                    json.dumps(
                        {
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "run_id": run_id,
                            "src": rec["dest"],
                            "dest": rec["src"],
                            "sha256": rec["sha256"],
                            "rule_id": rec["rule_id"],
                            "action": "rollback",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                restored += 1
    return {"run_id": run_id, "restored": restored}
