"""Make a Romanian NAMES.TXT (UTF-8 source) from the English file and the word list.

Usage: python apply_names.py EN_NAMES.TXT ro/glossary/names.tsv OUT_NAMES.TXT

Only the fields listed in the word list change; numbers, kept names, comments
and line layout stay exactly as in the English file. The one exception: a row
whose field is "+1", "+2", ... adds a new line before English line `line`
(its en column must equal that line, stripped). The Hall of Fame patch
ro-hof-colonies reads such lines from @INDEPENDENT.
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from textfiles import read_lines, table_fields  # noqa: E402


def load_glossary(path):
    rows = {}
    with open(path, encoding="utf-8") as f:
        lines = [l for l in f if not l.startswith("#")]
    for row in csv.DictReader(lines, delimiter="\t"):
        _, _, line, field = row["id"].split("/")
        rows[(int(line), field if field.startswith("+") else int(field))] = row
    return rows


def apply(en_lines, glossary):
    out = list(en_lines)
    inserts = {}
    for (line, field), row in glossary.items():
        if isinstance(field, str):
            if out[line].strip() != row["en"]:
                raise ValueError("line %d: new line goes before %r, found %r" % (line, row["en"], out[line]))
            inserts.setdefault(line, []).append((int(field[1:]), row["ro"]))
            continue
        spans = table_fields(out[line])
        idx, start, end, value = spans[field]
        if value != row["en"]:
            raise ValueError("line %d field %d: expected %r, found %r" % (line, field, row["en"], value))
        out[line] = out[line][:start] + row["ro"] + out[line][end:]
    for line in sorted(inserts, reverse=True):
        out[line:line] = [text for _, text in sorted(inserts[line])]
    return out


def main():
    en, gl, dest = sys.argv[1:4]
    lines = apply(read_lines(en), load_glossary(gl))
    with open(dest, "wb") as f:
        f.write("\r\n".join(lines).encode("utf-8"))
    print("wrote", dest)


if __name__ == "__main__":
    main()
