"""Split a dialog file into parts for translation, check a part, merge parts back.

  python sections.py split EN_FILE N OUT_DIR       -> OUT_DIR/part1.txt ... (English, latin-1 -> UTF-8)
  python sections.py check EN_FILE PART_FILE       -> checker errors for the sections in PART_FILE only
  python sections.py merge EN_FILE OUT_FILE PART_FILE...   -> EN_FILE with translated sections swapped in

A part file is plain UTF-8 text in the game's own format: '@NAME' header lines,
each followed by that section's lines, exactly as in the game file. A section
runs from its header to the next header, so comments and blank lines after a
section belong to it and must be kept.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check  # noqa: E402
from textfiles import classify  # noqa: E402


def read_text(path, encoding):
    with open(path, "rb") as f:
        data = f.read().decode(encoding)
    return data.replace("\r\n", "\n").split("\n")


def chunks(lines):
    """[(name or None, [lines])]: the preamble, then one chunk per section."""
    out = [(None, [])]
    for line in lines:
        if classify(line) == "section":
            out.append((line.strip()[1:], [line]))
        else:
            out[-1][1].append(line)
    return out


def split(en_file, n, out_dir):
    parts = chunks(read_text(en_file, "latin-1"))
    body = parts[1:]
    words = [sum(len(l.split()) for l in c) for _, c in body]
    target = sum(words) / n
    os.makedirs(out_dir, exist_ok=True)
    k, acc, cur = 1, 0, []
    for (name, c), w in zip(body, words):
        cur.append(c)
        acc += w
        if acc >= target * k and k < n:
            _write_part(out_dir, k, cur)
            k, cur = k + 1, []
    _write_part(out_dir, k, cur)


def _write_part(out_dir, k, chunk_list):
    path = os.path.join(out_dir, "part%d.txt" % k)
    lines = [l for c in chunk_list for l in c]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    names = [c[0].strip() for c in chunk_list]
    print("%s: %d sections, %s .. %s" % (path, len(names), names[0], names[-1]))


def trailing_blanks(c):
    n = 0
    while n < len(c) and c[len(c) - 1 - n] == "":
        n += 1
    return n


def merge(en_file, part_files):
    en = chunks(read_text(en_file, "latin-1"))
    new = {}
    for p in part_files:
        for name, c in chunks(read_text(p, "utf-8"))[1:]:
            new[name] = c
    out = []
    for name, c in en:
        if name in new:
            repl = list(new[name])
            # the separator blank lines after a section always come from English
            del repl[len(repl) - trailing_blanks(repl):]
            out.extend(repl + [""] * trailing_blanks(c))
        else:
            out.extend(c)
    return out, set(new)


def check_part(en_file, part_file):
    lines, names = merge(en_file, [part_file])
    tmp = tempfile.mkdtemp()
    try:
        name = os.path.basename(en_file)
        with open(os.path.join(tmp, name), "wb") as f:
            f.write("\r\n".join(lines).encode("utf-8"))
        errors, _, _ = check.run_check(os.path.dirname(en_file), tmp)
    finally:
        shutil.rmtree(tmp)
    # English passes its own check, so every error comes from this part
    return errors, names


def main():
    cmd = sys.argv[1]
    if cmd == "split":
        split(sys.argv[2], int(sys.argv[3]), sys.argv[4])
    elif cmd == "check":
        errors, names = check_part(sys.argv[2], sys.argv[3])
        for e in errors:
            print("ERROR", e)
        print("%d sections checked, %d errors" % (len(names), len(errors)))
        return 1 if errors else 0
    elif cmd == "merge":
        lines, names = merge(sys.argv[2], sys.argv[4:])
        with open(sys.argv[3], "wb") as f:
            f.write("\r\n".join(lines).encode("utf-8"))
        print("merged %d sections into %s" % (len(names), sys.argv[3]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
