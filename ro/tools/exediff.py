"""Anchored byte diff of two builds of VICEROY.EXE.

Finds long identical runs (anchors) to learn the offset shift between builds,
then lists the short regions that differ between anchors, with file offsets
in both files. Used to spot constants that changed between language builds.

Usage: python exediff.py A.EXE B.EXE [min_anchor]
"""
import sys
from difflib import SequenceMatcher


def diff(a, b, window=4096):
    """Walk both files in step; resync with SequenceMatcher on small windows."""
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        sa, sb = a[i:i + window], b[j:j + window]
        sm = SequenceMatcher(None, sa, sb, autojunk=False)
        blocks = [m for m in sm.get_matching_blocks() if m.size]
        if not blocks:
            out.append(("gap", i, j, sa[:16], sb[:16]))
            i += window
            j += window
            continue
        last_i, last_j = 0, 0
        for m in blocks:
            if m.a > last_i or m.b > last_j:
                out.append(("diff", i + last_i, j + last_j, sa[last_i:m.a], sb[last_j:m.b]))
            last_i, last_j = m.a + m.size, m.b + m.size
        # advance to the end of the last solid match, keep a margin for resync
        adv = max(last_i - 64, window // 2)
        i += adv
        j += adv + (blocks[-1].b - blocks[-1].a)
    return out


if __name__ == "__main__":
    a = open(sys.argv[1], "rb").read()
    b = open(sys.argv[2], "rb").read()
    seen = set()
    for kind, i, j, x, y in diff(a, b):
        key = (i, j)
        if key in seen:
            continue
        seen.add(key)
        print("%s A:%06x B:%06x  A=%s  B=%s" % (kind, i, j, x[:24].hex(" "), y[:24].hex(" ")))
