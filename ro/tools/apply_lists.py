"""Make a Romanian list file (LABELS.TXT, MENU.TXT...) from the English file and a
pairs file.

Usage: python apply_lists.py EN_FILE PAIRS.tsv OUT_FILE

PAIRS.tsv: one line per entry, in file order: english<TAB>romanian. Every entry
of every @section is listed, blank entries too (written as an empty english
column). The script checks each English entry matches before replacing it,
so an entry can never land in the wrong slot. Lines starting with '#' are notes.
Leading spaces of an entry (menu indentation) are kept from the English line.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from textfiles import classify, read_lines  # noqa: E402


def load_pairs(path):
    pairs = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if line.startswith("#"):
                continue
            if "\t" not in line:
                raise ValueError("%s:%d: need english<TAB>romanian" % (path, n))
            en, ro = line.split("\t", 1)
            pairs.append((en, ro, n))
    return pairs


def apply(en_lines, pairs):
    out, k, in_section = list(en_lines), 0, False
    for i, line in enumerate(en_lines):
        kind = classify(line)
        if kind == "section":
            in_section = True
            continue
        if not in_section or kind in ("comment", "directive", "disabled"):
            continue
        if k >= len(pairs):
            raise ValueError("pairs file ends before English line %d (%r)" % (i + 1, line))
        en, ro, n = pairs[k]
        if en.strip() != line.strip():
            raise ValueError("pairs line %d says %r but English line %d is %r" % (n, en, i + 1, line))
        indent = line[:len(line) - len(line.lstrip())]
        out[i] = (indent + ro) if line.strip() else ro
        k += 1
    if k != len(pairs):
        raise ValueError("%d pairs left over" % (len(pairs) - k))
    return out


def main():
    en, pairs, dest = sys.argv[1:4]
    lines = apply(read_lines(en), load_pairs(pairs))
    with open(dest, "wb") as f:
        f.write("\r\n".join(lines).encode("utf-8"))
    print("wrote", dest)


if __name__ == "__main__":
    main()
