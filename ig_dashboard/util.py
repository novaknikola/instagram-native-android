# -*- coding: utf-8 -*-
"""Shared safe helpers - never raise into the UI layer."""
from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def safe_int(value: Any, default: int = 0, lo: Optional[int] = None, hi: Optional[int] = None) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


PER_PAGE_CHOICES = (10, 25, 50, 100)


def normalize_per_page(value, default: int = 25) -> int:
    """Snap to customer page-size choices: 10 / 25 / 50 / 100."""
    n = safe_int(value, default, lo=1, hi=1000)
    if n in PER_PAGE_CHOICES:
        return n
    # nearest allowed size
    return min(PER_PAGE_CHOICES, key=lambda c: abs(c - n))


def paginate(items, page=1, per_page=25):
    """Slice a list for UI tables. Returns (slice, total, page, pages_n, per_page)."""
    try:
        seq = list(items or [])
    except Exception:
        seq = []
    per_page = normalize_per_page(per_page, 25)
    total = len(seq)
    pages_n = max(1, (total + per_page - 1) // per_page) if total else 1
    page = safe_int(page, 1, lo=1)
    if page > pages_n:
        page = pages_n
    start = (page - 1) * per_page
    return seq[start : start + per_page], total, page, pages_n, per_page


def read_text(path: Path, default: str = "") -> str:
    if not path or not Path(path).exists():
        return default
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return Path(path).read_text(encoding=enc, errors="strict")
        except Exception:
            continue
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return default


def iter_csv_rows(path: Path) -> Iterable[List[str]]:
    """Yield CSV rows; skip unreadable / empty. Never raises."""
    if not path or not Path(path).exists():
        return
    text = read_text(Path(path))
    if not text.strip():
        return
    try:
        for row in csv.reader(text.splitlines()):
            if row:
                yield row
    except Exception:
        return


def iter_csv_dicts(path: Path) -> Iterable[Dict[str, str]]:
    if not path or not Path(path).exists():
        return
    text = read_text(Path(path))
    if not text.strip():
        return
    try:
        for row in csv.DictReader(text.splitlines()):
            if row:
                yield {k: (v if v is not None else "") for k, v in row.items()}
    except Exception:
        return


def atomic_write_csv(path: Path, fieldnames: List[str], rows: List[Dict[str, Any]]) -> None:
    """Write CSV via temp file + replace (Windows-safe)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.stem + "_", suffix=".tmp", dir=str(path.parent)
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with open(tmp, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow(row)
        os.replace(str(tmp), str(path))
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass
        raise


def ensure_dir(path: Path) -> None:
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
