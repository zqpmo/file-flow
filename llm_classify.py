# -*- coding: utf-8 -*-
"""FF_LLM_CMD 桥：stdin JSON → 调用 OpenAI 兼容接口 → stdout {"results":[...]}。

凭据从同目录 secrets.env 读取（不入 git）：
  FF_LLM_BASE_URL=...
  FF_LLM_API_KEY=...
  FF_LLM_MODEL=...
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_MODEL = "your-model-id"


def load_secrets() -> dict:
    env_path = HERE / "secrets.env"
    data: dict[str, str] = {}
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("FF_LLM_BASE_URL", "FF_LLM_API_KEY", "FF_LLM_MODEL"):
        if os.environ.get(k):
            data[k] = os.environ[k]
    data.setdefault("FF_LLM_MODEL", DEFAULT_MODEL)
    return data


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 最外层花括号
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        return json.loads(m.group(0))
    raise ValueError(f"no JSON object in LLM output: {text[:200]}")


def chat(cfg: dict, user_prompt: str) -> str:
    base = cfg["FF_LLM_BASE_URL"].rstrip("/")
    url = f"{base}/chat/completions"
    body = {
        "model": cfg["FF_LLM_MODEL"],
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是文件归档分类器。只输出一个 JSON 对象，不要解释。"
                    "格式: {\"results\":[{\"name\":\"...\",\"dest_key\":\"...\",\"reason\":\"...\",\"confidence\":0.0-1.0}]}。"
                    "dest_key 必须来自用户给出的 dest_whitelist；无法判断时 confidence 给 0.3 且 dest_key=pending。"
                    "为 files 中每个文件输出一条 result，name 必须与输入完全一致。"
                ),
            },
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "max_tokens": 4096,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {cfg['FF_LLM_API_KEY']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e
    content = payload["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content)


def main() -> int:
    # Windows 控制台可能非 UTF-8：强制 stdin/stdout
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    prompt = sys.stdin.read()
    if not prompt.strip():
        print(json.dumps({"error": "empty stdin"}))
        return 1
    cfg = load_secrets()
    if not cfg.get("FF_LLM_BASE_URL") or not cfg.get("FF_LLM_API_KEY"):
        print(json.dumps({"error": "FF_LLM_BASE_URL/FF_LLM_API_KEY missing"}))
        return 1
    try:
        raw = chat(cfg, prompt)
        parsed = extract_json(raw)
        # 归一化：接受 results 数组或单对象
        if "results" not in parsed and "dest_key" in parsed:
            parsed = {"results": [parsed]}
        if "results" not in parsed:
            raise ValueError(f"missing results key: {str(parsed)[:200]}")
        # 只保留合法 dest_key，交给 ai_worker 二次白名单校验
        print(json.dumps(parsed, ensure_ascii=False))
        return 0
    except Exception as e:  # noqa: BLE001 — 桥失败时输出错误 JSON，由 worker 记 unresolved
        print(json.dumps({"error": str(e)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
