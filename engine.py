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


def _is_exempt(path: Path, zones: dict, root: Path | None = None) -> bool:
    name = path.name
    for p in zones.get("exempt_name_prefixes") or []:
        if name.startswith(p):
            return True
    if name.lower() == "desktop.ini":
        return True
    # Skip exempt dir names only under the scan root (not absolute path ancestors
    # like ...\AppData\Local\Temp\... which would wipe the whole tree).
    exempt_dirs = set(zones.get("exempt_dirs") or [])
    if exempt_dirs:
        try:
            rel_parts = path.resolve().relative_to(Path(root).resolve()).parts if root else path.parts
        except ValueError:
            rel_parts = path.parts
        for part in rel_parts[:-1]:  # directories only, not the filename
            if part in exempt_dirs:
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


def _safe_join_dest(dest_root: Path, sub: str) -> Path:
    """Join subfolder under dest_root; reject path escape (e.g. ../outside)."""
    root = Path(dest_root)
    if not sub:
        return root
    candidate = (root / sub).resolve()
    root_res = root.resolve()
    try:
        candidate.relative_to(root_res)
    except ValueError as e:
        raise ValueError(
            f"subfolder escapes destination root: sub={sub!r} root={root_res}"
        ) from e
    return candidate


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
    # Ensure runtime dirs exist before any move or state write (first-run safety).
    state_dir = ff_root / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    run_dir = ff_root / "runs" / run_id
    journal_path = run_dir / "journal.ndjson"
    report_path = run_dir / "run-report.json"
    state_path = state_dir / "seen_hashes.json"
    seen: dict = {}
    if state_path.exists():
        seen = json.loads(state_path.read_text(encoding="utf-8"))

    moves: list[dict] = []
    pending: list[dict] = []
    ignored: list[dict] = []
    skipped_plan: list[dict] = []
    dest_roots = {k: Path(v) for k, v in zones["dest_roots"].items()}

    for zone_key, zone_path in _iter_sources(zones, source_filter, extra_sources):
        max_depth = 0 if zone_key == "home_root" else None
        for src in list(_iter_files(zone_path, max_depth)):
            if _is_exempt(src, zones, root=zone_path):
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
                try:
                    target_dir = _safe_join_dest(dest_roots[dest_key], sub)
                except ValueError as ex:
                    skipped_plan.append(
                        {
                            "src": str(src),
                            "dest_key": dest_key,
                            "rule_id": verdict["rule_id"],
                            "error": str(ex),
                        }
                    )
                    continue
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
                suggested = str(dest_roots[dest_key] / src.name)
                if sub:
                    try:
                        suggested = str(_safe_join_dest(dest_roots[dest_key], sub) / src.name)
                    except ValueError:
                        suggested = str(dest_roots[dest_key] / src.name)
                pending.append(
                    {
                        "src": str(src),
                        "dest": str(queue_dest),
                        "suggested_dest": suggested,
                        "dest_key": dest_key,
                        "rule_id": verdict["rule_id"],
                        "confidence": verdict["confidence"],
                        "decision": verdict["decision"],
                    }
                )

    moved_ok = pending_ok = 0
    move_failed = pending_failed = 0
    if not dry_run:
        run_dir.mkdir(parents=True, exist_ok=True)
        skipped: list[dict] = list(skipped_plan)
        with open(journal_path, "a", encoding="utf-8") as jf:
            for e in moves:
                try:
                    final = _unique_dest(Path(e["dest"]))
                    final.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(e["src"], final)
                except OSError as ex:
                    skipped.append({"src": e["src"], "error": str(ex)})
                    move_failed += 1
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
                moved_ok += 1
            for e in pending:
                try:
                    final = _unique_dest(Path(e["dest"]))
                    final.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(e["src"], final)
                except OSError as ex:
                    skipped.append({"src": e["src"], "error": str(ex)})
                    pending_failed += 1
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
                seen[rec["sha256"]] = "done"
                pending_ok += 1
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
        "moved": 0 if dry_run else moved_ok,
        "would_move": len(moves),
        "move_failed": 0 if dry_run else move_failed,
        "pending_ai": len(pending) if dry_run else pending_ok,
        "pending_failed": 0 if dry_run else pending_failed,
        "partial": bool(
            not dry_run
            and (
                move_failed
                or pending_failed
                or len(skipped_plan) > 0
            )
        ),
        "ignored": len(ignored),
        "moves": moves,
        "pending": pending,
        "skipped_plan": skipped_plan,
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
    skipped: list[dict] = []
    text = journal_path.read_text(encoding="utf-8")
    lines = [json.loads(x) for x in text.splitlines() if x.strip()]
    # Idempotency: pairs already restored in this journal must not run again.
    already: set[tuple[str, str]] = set()
    for r in lines:
        if r.get("action") == "rollback":
            # rollback record stores original dest in "src" and original src in "dest"
            already.add((str(r.get("dest", "")), str(r.get("src", ""))))
    state_path = ff_root / "state" / "seen_hashes.json"
    seen: dict = {}
    if state_path.exists():
        seen = json.loads(state_path.read_text(encoding="utf-8"))
    with open(journal_path, "a", encoding="utf-8") as jf:
        # LIFO: restore later moves first (safer for nested destinations)
        for rec in reversed(lines):
            if rec.get("action") not in ("move", "to_pending"):
                continue
            dest, src = Path(rec["dest"]), Path(rec["src"])
            key = (str(src), str(dest))
            if key in already:
                skipped.append(
                    {"src": str(src), "dest": str(dest), "reason": "already_rolled_back"}
                )
                continue
            if not dest.exists():
                skipped.append({"src": str(src), "reason": "dest_missing"})
                continue
            if src.exists():
                # Never overwrite a newer file at the original path.
                skipped.append(
                    {
                        "src": str(src),
                        "dest": str(dest),
                        "reason": "original_path_occupied",
                    }
                )
                continue
            expected = rec.get("sha256") or ""
            if expected:
                actual = _sha256(dest)
                if actual != expected:
                    # Destination content changed since the move — do not pull it back.
                    skipped.append(
                        {
                            "src": str(src),
                            "dest": str(dest),
                            "reason": "dest_hash_mismatch",
                            "expected_sha256": expected,
                            "actual_sha256": actual,
                        }
                    )
                    continue
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dest), str(src))
            digest = rec.get("sha256")
            if digest and seen.get(digest) == "done":
                del seen[digest]
            jf.write(
                json.dumps(
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "run_id": run_id,
                        "src": rec["dest"],
                        "dest": rec["src"],
                        "sha256": rec.get("sha256", ""),
                        "rule_id": rec.get("rule_id", ""),
                        "action": "rollback",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            already.add(key)
            restored += 1
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "run_id": run_id,
        "restored": restored,
        "skipped": skipped,
    }
