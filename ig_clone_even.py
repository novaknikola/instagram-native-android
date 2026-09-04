# -*- coding: utf-8 -*-
# Even unique-clone spread: 180 clones / 20 phones → 9 each (not 20+18+3).
# One APK still maps to exactly one phone. Does not uninstall.

def even_counts(haves, unused, cap=20):
    """Return target unique-count per phone after adding `unused` installs.

    Always increment the current poorest phone that is still under `cap`.
    Phones already above the even level keep their count (no uninstall).
    Targets on one fleet differ by at most 1 among phones that received adds,
    except phones that were already fat stay fat.
    """
    n = len(haves or [])
    if n == 0:
        return []
    try:
        cap = max(1, int(cap))
    except (TypeError, ValueError):
        cap = 20
    try:
        left = max(0, int(unused))
    except (TypeError, ValueError):
        left = 0
    targets = [max(0, int(h or 0)) for h in haves]
    while left > 0:
        elig = [i for i in range(n) if targets[i] < cap]
        if not elig:
            break
        i = min(elig, key=lambda j: (targets[j], j))
        targets[i] += 1
        left -= 1
    return targets


def even_plan(haves, unused, cap=20):
    """Human summary for logs / dashboard."""
    haves = [max(0, int(h or 0)) for h in (haves or [])]
    targets = even_counts(haves, unused, cap=cap)
    adds = [max(0, t - h) for t, h in zip(targets, haves)]
    n = len(haves)
    phones = n
    have_total = sum(haves)
    after_total = sum(targets)
    lo = min(targets) if targets else 0
    hi = max(targets) if targets else 0
    have_lo = min(haves) if haves else 0
    have_hi = max(haves) if haves else 0
    return {
        "phones": phones,
        "cap": cap,
        "unused": max(0, int(unused or 0)),
        "have_total": have_total,
        "after_total": after_total,
        "have_lo": have_lo,
        "have_hi": have_hi,
        "target_lo": lo,
        "target_hi": hi,
        "adds": adds,
        "targets": targets,
        "balanced_after": (hi - lo) <= 1 if targets else True,
        "even_each": lo if lo == hi else "%s–%s" % (lo, hi),
    }
