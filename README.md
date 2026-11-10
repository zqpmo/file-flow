# file-flow

> 中文：规则优先 + 可选 LLM 分类的**文件入站引擎**——扫描、归类、受控搬移、journal 可回滚；全程支持 dry-run。

**Rule-first file inbound engine** with optional LLM classification: scan → classify → controlled move → journaled, **rollback-capable**, **dry-run by default** (`--execute` to move). Engine has **zero third-party Python dependencies**.

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Tests](https://img.shields.io/badge/tests-unittest-orange)](#testing)

## Why this exists

Dropping files into Downloads/Desktop and sorting them by hand does not scale. `file-flow` turns that into a **deterministic pipeline**:

1. **Scan** configured source zones
2. **Classify** by priority-ordered rules (filename / extension match)
3. **Move** only into a **destination whitelist** (`zones.json` `dest_roots`)
4. **Journal** every move as NDJSON under `runs/<run_id>/`
5. Optionally drain a **pending queue** with an LLM (`--ai`) under the same whitelist

Uncertain matches go to `pending` for human or AI review — the engine never invents destinations outside the whitelist.

## Features

| Capability | Behavior |
|---|---|
| Dry-run by default | Plain `python cli.py` only previews (`would_move`). **Moves require `--execute`.** |
| Priority rules | First match by `priority` descending; catch-all `default-pending` required |
| Dest whitelist | Rule `destination` must exist in `zones.dest_roots` or the rule raises |
| SHA-256 state | `state/seen_hashes.json` skips already-processed files (dirs auto-created) |
| Journal | `runs/<id>/journal.ndjson` records `move` / `to_pending` |
| Rollback | `--rollback <run_id>` restores files; **refuses to overwrite** an existing file at the original path; clears restored hashes from `seen` |
| Exempt dirs | Path segments in `zones.exempt_dirs` (e.g. `AppData`) are skipped during recursive scan |
| Optional LLM | `--ai` drains `pending` via `FF_LLM_CMD` / `llm_classify.py`; AI moves get their own journal (`runs/<ai_run_id>/`) |
| Exemptions | Lock files (`~$`), shortcuts (`.lnk`), OS junk prefixes/dirs |

## Quick start

```bash
# 1) Layout (example paths — edit zones.json for your machine)
mkdir -p data/{downloads,desktop,inbox,projects,training,knowledge,capture,pending,archive,scratch,runs}

# 2) Dry-run (default — no files move)
python cli.py

# 3) Single pass (writes + journal) — explicit opt-in
python cli.py --execute

# 4) Optional: AI drain of pending/
#    cp secrets.env.example secrets.env   # fill values (gitignored)
python cli.py --execute --ai

# 5) Rollback a previous run (engine run_id or AI run_id from the JSON report)
python cli.py --rollback <run_id>
```

PowerShell helper:

```powershell
.\runner.ps1              # dry-run (default)
.\runner.ps1 -Execute     # actually move
.\runner.ps1 -Execute -AI
.\runner.ps1 -Rollback <run_id>
```

## Configuration

### `zones.json`

- **`sources`** — directories to scan (key → path).
- **`dest_roots`** — **whitelist** of allowed destinations (key → path). Rules and LLM output may only use these keys.
- **`exempt_name_prefixes` / `exempt_dirs`** — never touch these.
- Paths in this repo are **relative examples** (`./data/...`). Point them at your real folders before a live run.

### `rules.json`

Priority-ordered matchers:

```json
{
  "id": "training-deck",
  "priority": 55,
  "match": { "name_any": ["training"], "extensions": [".pptx", ".pdf"] },
  "destination": "training",
  "confidence": 0.88
}
```

- `decision` optional: if omitted, derived from `confidence` (`≥0.85` → `auto`, else `pending_ai`).
- Last rule must be a catch-all (`"match": {}`) so classification never raises unexpectedly.

### LLM credentials (optional)

Copy `secrets.env.example` → `secrets.env` (gitignored):

```
FF_LLM_BASE_URL=...
FF_LLM_API_KEY=...
FF_LLM_MODEL=...
```

Or set the same names as environment variables. `llm_classify.py` is an OpenAI-compatible `/chat/completions` bridge (stdin JSON → stdout `{"results":[...]}`).

## Architecture

```
engine.py         # scan / classify / move / journal / rollback (stdlib only)
cli.py            # argparse entry: --dry-run --zone --rollback --ai
ai_worker.py      # pending/ → LLM → whitelist-checked moves
llm_classify.py   # optional OpenAI-compatible bridge
runner.ps1        # thin PowerShell wrapper
zones.json        # sources + dest whitelist + exemptions
rules.json        # priority rules
tests/            # unittest suite (no network)
```

### Safety boundaries

- Destinations outside `dest_roots` are rejected (rules raise; AI results ignored unless `confidence ≥ 0.8` **and** key is whitelisted).
- AI failures never block the rule engine — files stay in `pending`.
- **Default is dry-run.** Only `--execute` moves files on disk.
- Rollback **never overwrites** a file that already exists at the original path (it reports `original_path_occupied` and skips).
- This tool only **moves files you already own** on the local machine.

### Privacy when `--ai` is enabled

The AI path sends **file names, full paths, sizes, and destination keys** to the LLM endpoint you configure (`FF_LLM_BASE_URL` / `FF_LLM_CMD`). It does **not** upload file contents — but names and paths can still be sensitive. Do not enable `--ai` against an untrusted endpoint.

## Testing

```bash
python -m unittest discover -s tests -v
```

Covers: rule priority, lockfile ignore, dry-run no-move, journal + rollback, AI whitelist rejection, low-confidence stays pending.

## License

[MIT](./LICENSE)
