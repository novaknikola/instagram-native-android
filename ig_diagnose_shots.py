# -*- coding: utf-8 -*-
"""
from farm_root import ROOT
ig_diagnose_shots.py - Offline review of error shots (optional Grok/xAI vision).

Usage:
  python ig_diagnose_shots.py
  python ig_diagnose_shots.py --dir C:\\threads-android\\logs\\ig_run_2026-08-10_091500
  python ig_diagnose_shots.py --limit 10

Writes diagnosis.md in the shot dir. If XAI_API_KEY or GROK_API_KEY is set,
attempts vision analysis. Otherwise writes a structured manual checklist.
Never taps phones. Never required for the farm to run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

try:
    import ig_error_shots as es
except Exception:
    es = None


def _load_rows(directory: Path, limit: int):
    if es is not None:
        return es.list_shots(limit=limit, directory=str(directory))
    idx = directory / "index.jsonl"
    rows = []
    if not idx.exists():
        return rows
    for ln in reversed(idx.read_text(encoding="utf-8", errors="replace").splitlines()):
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln))
        except Exception:
            continue
        if len(rows) >= limit:
            break
    return rows


def _manual_block(rows) -> str:
    lines = [
        "# Error-shot diagnosis (manual / offline)",
        "",
        "Generated: %s" % datetime.now().isoformat(timespec="seconds"),
        "",
        "Rule: use these shots to **add detectors in ig_loop**, not to auto-tap UNKNOWN.",
        "",
    ]
    for i, r in enumerate(rows, 1):
        lines.append("## %d. %s · %s · %s" % (
            i, r.get("result") or "?", r.get("username") or "-", r.get("serial") or "-"))
        lines.append("- time: %s" % (r.get("ts") or ""))
        lines.append("- note: %s" % (r.get("note") or ""))
        lines.append("- png: `%s`" % (r.get("png_name") or r.get("png") or ""))
        lines.append("- xml: `%s`" % (r.get("xml_name") or r.get("xml") or ""))
        lines.append("- likely screen: _(open PNG / XML and fill)_")
        lines.append("- suggested ig_loop fix: _(detector / dismiss)_")
        lines.append("")
    return "\n".join(lines)


def _grok_analyze(rows, directory: Path) -> str:
    key = (os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY") or "").strip()
    if not key:
        return ""
    # Best-effort: summarize text from XML snippets; PNG vision depends on provider API.
    # Keep soft-fail — farm must not depend on this.
    parts = [
        "# Error-shot diagnosis (Grok/xAI assist)",
        "",
        "Generated: %s" % datetime.now().isoformat(timespec="seconds"),
        "",
        "Advisory only. Do not auto-tap from this output.",
        "",
    ]
    for i, r in enumerate(rows[:8], 1):
        xml_path = r.get("xml") or ""
        snippet = ""
        if xml_path and Path(xml_path).is_file():
            try:
                raw = Path(xml_path).read_text(encoding="utf-8", errors="replace")
                # Pull text= attributes
                import re
                texts = re.findall(r'text="([^"]{2,80})"', raw)
                snippet = " | ".join(texts[:40])
            except Exception:
                snippet = ""
        prompt = (
            "Instagram Android farm automation failed.\n"
            "result=%s username=%s serial=%s note=%s\n"
            "UI text dump: %s\n"
            "In 5 bullets: (1) likely screen (2) why fail (3) safe dismiss labels "
            "(4) ig_loop detector idea (5) what NOT to auto-tap.\n"
        ) % (
            r.get("result"), r.get("username"), r.get("serial"), r.get("note"),
            snippet[:1500],
        )
        advice = _xai_chat(key, prompt)
        parts.append("## %d. %s · %s" % (i, r.get("result"), r.get("username")))
        parts.append(advice or "_(API call failed — use PNG/XML manually)_")
        parts.append("")
    out = "\n".join(parts)
    try:
        (directory / "diagnosis_grok.md").write_text(out, encoding="utf-8")
    except Exception:
        pass
    return out


def _xai_chat(api_key: str, prompt: str) -> str:
    """Minimal xAI chat completions call (text). Soft-fail."""
    try:
        body = json.dumps({
            "model": os.environ.get("XAI_MODEL", "grok-2-latest"),
            "messages": [
                {"role": "system", "content": "You help debug Instagram Android UI automation. Be concise."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.x.ai/v1/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer %s" % api_key,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
    except Exception as e:
        return "(xAI error: %s)" % e


def main():
    ap = argparse.ArgumentParser(description="Diagnose IG error shots offline")
    ap.add_argument("--dir", default="", help="Shot directory (default: IG_ERROR_SHOT_DIR or loose)")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    if args.dir:
        d = Path(args.dir)
    elif os.environ.get("IG_ERROR_SHOT_DIR"):
        d = Path(os.environ["IG_ERROR_SHOT_DIR"])
    else:
        base = Path(os.environ.get("IG_FARM_BASE", ROOT))
        # Prefer newest ig_run_* folder under logs/
        logs = base / "logs"
        d = base / "logs" / "error_shots" / "loose"
        if logs.is_dir():
            runs = sorted(
                [p for p in logs.iterdir() if p.is_dir() and p.name.startswith("ig_run_")],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if runs:
                d = runs[0]

    if not d.is_dir():
        print("No shot dir: %s" % d)
        sys.exit(1)

    rows = _load_rows(d, args.limit)
    print("Shot dir: %s (%d incidents)" % (d, len(rows)))
    manual = _manual_block(rows)
    out_path = d / "diagnosis.md"
    out_path.write_text(manual, encoding="utf-8")
    print("Wrote %s" % out_path)
    grok = _grok_analyze(rows, d)
    if grok:
        print("Also wrote diagnosis_grok.md (API key present)")
    else:
        print("No XAI_API_KEY/GROK_API_KEY — manual diagnosis.md only (open PNGs).")


if __name__ == "__main__":
    main()
