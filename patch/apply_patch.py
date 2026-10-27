#!/usr/bin/env python3
"""
Binary patches for Sid Meier's Colonization, MS-DOS version 3.0.

Reads patches.json (next to this script) and applies the patches you select to
a copy of VICEROY.EXE that you already own. No game code is distributed with
this tool.

    python3 apply_patch.py VICEROY.EXE --list
    python3 apply_patch.py VICEROY.EXE --status
    python3 apply_patch.py VICEROY.EXE --all
    python3 apply_patch.py VICEROY.EXE traderoute-topup
    python3 apply_patch.py VICEROY.EXE traderoute-topup --revert
    python3 apply_patch.py VICEROY.EXE --manifest other.json --list

--manifest reads another patch list (same format) instead of patches.json,
for example an optional set kept apart from the bug fixes.

Safety rules:
  * Every byte at every patch site is checked before anything is written.
    If one site holds unexpected bytes, nothing is written.
  * --all applies only patches with status "recommended". An "experimental"
    patch is applied only when you name it.
  * Before writing, the current file is copied to VICEROY.EXE.<md5:8>.bak
    (if that file does not exist yet). Every state you ever had is kept, and
    the name tells you which state it is. The pristine file is
    VICEROY.EXE.0f5d5b00.bak.
  * The new file is written to a temporary file and then renamed over the
    old one, so an interrupted run never leaves a half-written EXE.

Exit codes:
  0 success or nothing to do   1 file not found      2 bad command line
  3 unexpected bytes on disk   5 conflicting patches 6 invalid manifest
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "patches.json"
STATUSES = ("recommended", "experimental")

EXIT_OK, EXIT_NOFILE, EXIT_USAGE, EXIT_BYTES, EXIT_CONFLICT, EXIT_MANIFEST = 0, 1, 2, 3, 5, 6


class ManifestError(Exception):
    pass


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def load_manifest(path: Path) -> dict:
    """Load and validate patches.json. Anything unexpected is an error."""
    try:
        man = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ManifestError(f"cannot read {path}: {e}")

    if man.get("schema") != 2:
        raise ManifestError(f"unsupported schema {man.get('schema')!r} (expected 2)")
    tgt = man.get("target", {})
    for k in ("name", "version", "size", "md5_pristine"):
        if k not in tgt:
            raise ManifestError(f"target.{k} missing")

    seen = set()
    for p in man.get("patches", []):
        pid = p.get("id")
        for k in ("id", "title", "status", "offset", "original", "patched",
                  "asm_before", "asm_after", "md5_alone", "summary"):
            if k not in p:
                raise ManifestError(f"patch {pid!r}: field {k!r} missing")
        if pid in seen:
            raise ManifestError(f"patch id {pid!r} appears twice")
        seen.add(pid)
        if p["status"] not in STATUSES:
            raise ManifestError(f"patch {pid!r}: status {p['status']!r} is not one of {STATUSES}")
        try:
            p["_off"] = int(p["offset"], 16)
            p["_orig"] = bytes.fromhex(p["original"])
            p["_new"] = bytes.fromhex(p["patched"])
        except (TypeError, ValueError) as e:
            raise ManifestError(f"patch {pid!r}: bad hex value ({e})")
        if len(p["_orig"]) != len(p["_new"]) or not p["_orig"]:
            raise ManifestError(f"patch {pid!r}: original and patched must be the same non-zero length")
        if p["_off"] + len(p["_orig"]) > tgt["size"]:
            raise ManifestError(f"patch {pid!r}: site is past the end of the target")
        p.setdefault("conflicts", [])

    ids = {p["id"] for p in man["patches"]}
    for p in man["patches"]:
        for c in p["conflicts"]:
            if c not in ids:
                raise ManifestError(f"patch {p['id']!r}: conflicts with unknown id {c!r}")

    spans = sorted((p["_off"], p["_off"] + len(p["_orig"]), p["id"]) for p in man["patches"])
    for (a0, a1, aid), (b0, b1, bid) in zip(spans, spans[1:]):
        if b0 < a1:
            conf = {c for p in man["patches"] if p["id"] in (aid, bid) for c in p["conflicts"]}
            if aid not in conf and bid not in conf:
                raise ManifestError(f"patches {aid!r} and {bid!r} overlap but do not declare a conflict")
    return man


def state_of(data: bytes, p: dict) -> str:
    cur = data[p["_off"]:p["_off"] + len(p["_orig"])]
    if cur == p["_new"]:
        return "applied"
    if cur == p["_orig"]:
        return "not applied"
    return "UNEXPECTED"


def backup_path(exe: Path, data: bytes) -> Path:
    return exe.with_name(f"{exe.name}.{md5(data)[:8]}.bak")


def atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def cmd_list(man: dict) -> int:
    print(f"Target: {man['target']['version']}\n")
    for p in man["patches"]:
        print(f"  {p['id']}   [{p['status']}]")
        print(f"      {p['title']}")
        print(f"      {p['offset']}: {p['original']} -> {p['patched']}"
              f"   ({p['asm_before']}  =>  {p['asm_after']})")
        print(f"      {p['summary']}")
        if p.get("caveat"):
            print(f"      CAVEAT: {p['caveat']}")
        print()
    return EXIT_OK


def cmd_status(man: dict, exe: Path, data: bytes) -> int:
    digest = md5(data)
    print(f"file : {exe}")
    print(f"md5  : {digest}" + ("   (pristine)" if digest == man["target"]["md5_pristine"] else ""))
    for p in man["patches"]:
        print(f"  {p['id']:22s} {state_of(data, p):12s} [{p['status']}]")
    return EXIT_OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("exe", type=Path, help="path to VICEROY.EXE")
    ap.add_argument("ids", nargs="*", help="patch ids to apply or revert")
    ap.add_argument("--all", action="store_true",
                    help="select every patch with status 'recommended'")
    ap.add_argument("--revert", action="store_true", help="undo instead of apply")
    ap.add_argument("--list", action="store_true", help="describe the available patches")
    ap.add_argument("--status", "--check", dest="status", action="store_true",
                    help="report what is applied; change nothing")
    ap.add_argument("--manifest", type=Path, default=MANIFEST,
                    help="patch list to use (default: patches.json next to this script)")
    ap.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    try:
        man = load_manifest(args.manifest.resolve())
    except ManifestError as e:
        print(f"error: invalid {args.manifest.name}: {e}", file=sys.stderr)
        return EXIT_MANIFEST
    patches = man["patches"]
    by_id = {p["id"]: p for p in patches}

    if args.list:
        return cmd_list(man)

    if args.force:
        print("note: --force is no longer needed; every patch site is verified byte by byte.",
              file=sys.stderr)

    exe = args.exe.resolve()
    if not exe.is_file():
        print(f"error: no such file: {args.exe}", file=sys.stderr)
        return EXIT_NOFILE
    data = exe.read_bytes()

    if len(data) != man["target"]["size"]:
        print(f"error: {exe} is {len(data)} bytes; {man['target']['name']} "
              f"v3.0 is {man['target']['size']}. Refusing.", file=sys.stderr)
        return EXIT_BYTES

    if args.status:
        return cmd_status(man, exe, data)

    unknown = [i for i in args.ids if i not in by_id]
    if unknown:
        print(f"error: unknown patch id(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"known: {', '.join(by_id)}", file=sys.stderr)
        return EXIT_USAGE
    if args.all and args.ids:
        print("error: use either --all or patch ids, not both", file=sys.stderr)
        return EXIT_USAGE

    if args.all:
        selected = [p for p in patches if p["status"] == "recommended"]
        for p in patches:
            if p["status"] != "recommended" and not args.revert:
                print(f"  {p['id']:22s} skipped [{p['status']}] - name it to apply it")
        if args.revert:
            selected = list(patches)
    else:
        selected = [by_id[i] for i in dict.fromkeys(args.ids)]
    if not selected:
        print("Nothing selected. Use --all, --list, or name a patch id.", file=sys.stderr)
        return EXIT_USAGE

    # Every selected site must hold exactly the original or the patched bytes.
    for p in selected:
        if state_of(data, p) == "UNEXPECTED":
            cur = data[p["_off"]:p["_off"] + len(p["_orig"])]
            print(f"error: {p['id']}: unexpected bytes at {p['offset']}: {cur.hex()}\n"
                  f"       expected {p['original']} (original) or {p['patched']} (patched).\n"
                  f"       Refusing to write anything.", file=sys.stderr)
            return EXIT_BYTES

    if not args.revert:
        applied_after = {p["id"] for p in patches if state_of(data, p) == "applied"}
        applied_after |= {p["id"] for p in selected}
        for p in selected:
            clash = set(p["conflicts"]) & applied_after
            clash |= {q["id"] for q in patches
                      if q["id"] in applied_after and p["id"] in q["conflicts"]}
            if clash:
                print(f"error: {p['id']} conflicts with {', '.join(sorted(clash))}. "
                      f"Revert the other one first.", file=sys.stderr)
                return EXIT_CONFLICT

    new = bytearray(data)
    want = "applied" if args.revert else "not applied"
    changed = []
    for p in selected:
        if state_of(data, p) != want:
            print(f"  {p['id']:22s} already {'reverted' if args.revert else 'applied'}")
            continue
        frm, to = (p["_new"], p["_orig"]) if args.revert else (p["_orig"], p["_new"])
        new[p["_off"]:p["_off"] + len(to)] = to
        changed.append((p, frm, to))

    if not changed:
        print("Nothing to do.")
        return EXIT_OK

    bak = backup_path(exe, data)
    if bak.exists():
        print(f"backup : {bak.name} (already there)")
    else:
        shutil.copy2(exe, bak)
        print(f"backup : {bak.name}")

    atomic_write(exe, bytes(new))
    for p, frm, to in changed:
        verb = "reverted" if args.revert else "applied"
        print(f"  {p['id']:22s} {verb}  {p['offset']}: {frm.hex()} -> {to.hex()}")
    digest = md5(bytes(new))
    print(f"md5    : {digest}" + ("   (pristine)" if digest == man["target"]["md5_pristine"] else ""))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
