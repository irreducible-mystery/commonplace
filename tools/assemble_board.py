#!/usr/bin/env python3
"""assemble_board.py: BOARD.md from one file per entry.

Usage:
  python3 tools/assemble_board.py --check      # exit 1 if BOARD.md is out of date
  python3 tools/assemble_board.py --write      # regenerate BOARD.md
  python3 tools/assemble_board.py --split      # one-time migration, BOARD.md -> fragments
  python3 tools/assemble_board.py --verify     # split to a temp dir, reassemble, diff

WHY

`BOARD.md` has been emptied ten times on `main`, every time by a commit that
meant to add one entry (#209). `board_append.py` makes the append safe and
`check_board_history.py` catches a loss after the fact, but both are guards
around a shape that stays dangerous: one file, many writers, each holding the
whole thing in order to add to the end.

Fragments remove the shape instead of guarding it. **Creating a file cannot
truncate anything.** A session that adds an entry writes a new file and never
opens the others; a session that loses its context cannot take the record with
it. "Keep both" stops being a merge rule and becomes the default — two entries
under one heading are two files, and assembly concatenates them.

ORDER IS THE WHOLE PROBLEM, AND IT IS NOT THE TIMESTAMP

The obvious design names fragments by timestamp and sorts. That would silently
rewrite the record. The board holds **26 entries that are out of timestamp
order**, `check_board.py` lists them on every run, and `RULES.md` keeps them on
purpose: "that is permanent — the record is not rewritten". Sorting by stamp
erases all 26 and prints a cleaner board as if nothing had happened. (This is
not hypothetical: the restore in #208 was written that way first, reported 0
notes instead of 26, and had to be redone in append order.)

So each fragment carries a four-digit **sequence** — its position in the board,
not its time. Assembly sorts by `(sequence, filename)`. The sequence is a sort
key and not an identity: two sessions that both grab `0399` produce two
different files that git merges cleanly and assembly keeps, in filename order,
side by side. That is the keep-both rule falling out of the design rather than
being enforced by one.

WHAT BECOMES THE RECORD

`board/entries/` becomes the source and `BOARD.md` becomes derived, which is a
change to `RULES.md`, not only to the tooling — that file currently lists
`BOARD.md` among the sources that are never removed. The pull request proposes
the edit explicitly rather than letting the tooling decide it quietly. Until it
is agreed, `BOARD.md` stays committed and is regenerated, exactly as `docs/` is.

Exit codes:
  0  up to date / written / verified
  1  out of date (--check), or the round trip is not byte-identical (--verify)
  2  could not read the board or the fragments — a failed probe, never a pass
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

ENTRIES = "board/entries"
PREAMBLE = "board/PREAMBLE.md"
FRAGMENT = re.compile(r"^(\d{4})-")
HEADING = re.compile(r"^### \s*(\d{4}-\d{2}-\d{2})\s+(\d{2}):(\d{2})\s*UTC\s*·\s*([^·]+?)\s*·")


class ProbeFailure(Exception):
    """Could not read what was asked for. Not a finding about the board."""


def split_entries(text: str) -> tuple[str, list[str]]:
    """(preamble, [entry text, ...]). Preamble is everything before the first heading."""
    lines = text.split("\n")
    first = next((i for i, l in enumerate(lines) if l.startswith("### ")), len(lines))
    preamble = "\n".join(lines[:first]).rstrip("\n")
    entries: list[list[str]] = []
    for line in lines[first:]:
        if line.startswith("### "):
            entries.append([line])
        elif entries:
            entries[-1].append(line)
    return preamble, ["\n".join(e).rstrip() for e in entries]


def slug(entry: str) -> str:
    """A filename hint from the heading: date, time and session. Never an identity."""
    m = HEADING.match(entry.split("\n")[0])
    if not m:
        return "entry"
    date, hh, mm, session = m.groups()
    session = unicodedata.normalize("NFKD", session).encode("ascii", "ignore").decode()
    session = re.sub(r"[^A-Za-z0-9]+", "-", session).strip("-").lower() or "session"
    return f"{date.replace('-', '')}-{hh}{mm}-{session}"[:72]


def fragment_names(entries: list[str]) -> list[str]:
    """Sequence-prefixed names, unique within this split."""
    names, used = [], set()
    for i, e in enumerate(entries, start=1):
        base = f"{i:04d}-{slug(e)}"
        name, n = f"{base}.md", 2
        while name in used:
            name, n = f"{base}-{n}.md", n + 1
        used.add(name)
        names.append(name)
    return names


def read_fragments(root: Path) -> tuple[str, list[tuple[str, str]]]:
    """(preamble, [(name, text)]) in board order."""
    d = root / ENTRIES
    if not d.is_dir():
        raise ProbeFailure(f"{ENTRIES}/ does not exist — nothing to assemble from")
    files = sorted(p for p in d.iterdir() if p.suffix == ".md")
    if not files:
        raise ProbeFailure(f"{ENTRIES}/ is empty — a failed read, not a board with no entries")
    bad = [p.name for p in files if not FRAGMENT.match(p.name)]
    if bad:
        raise ProbeFailure(
            f"{len(bad)} fragment(s) have no NNNN- sequence prefix and have no place in the "
            f"order: {', '.join(bad[:5])}"
        )
    # (sequence, filename): the sequence orders the board, the filename breaks
    # ties so two sessions that grabbed the same number both keep their entry.
    files.sort(key=lambda p: (int(FRAGMENT.match(p.name).group(1)), p.name))
    pre = root / PREAMBLE
    preamble = pre.read_text(encoding="utf-8").rstrip("\n") if pre.exists() else ""
    return preamble, [(p.name, p.read_text(encoding="utf-8").rstrip()) for p in files]


# How the pieces join, stated once so it cannot drift: the preamble is stored
# with trailing newlines stripped and followed by exactly one newline; each
# entry is stored stripped and followed by exactly one blank line; the file
# ends with a single newline. Measured against the board rather than assumed —
# all 397 inter-entry gaps in it are exactly one blank line, and `--verify`
# re-checks the whole round trip byte for byte on every run.
def assemble(root: Path) -> str:
    preamble, frags = read_fragments(root)
    body = "".join(text + "\n\n" for _, text in frags)
    return ((preamble + "\n") if preamble else "") + body.rstrip() + "\n"


def split(root: Path, board_text: str, out: Path | None = None) -> list[str]:
    """Write one file per entry. Returns the filenames written, in order."""
    target = out or root
    d = target / ENTRIES
    d.mkdir(parents=True, exist_ok=True)
    preamble, entries = split_entries(board_text)
    if not entries:
        raise ProbeFailure("the board parsed to zero entries — the parser is broken, not the board")
    names = fragment_names(entries)
    for name, text in zip(names, entries):
        (d / name).write_text(text + "\n", encoding="utf-8")
    pre = target / PREAMBLE
    pre.parent.mkdir(parents=True, exist_ok=True)
    pre.write_text(preamble + "\n" if preamble else "", encoding="utf-8")
    return names


def verify(root: Path, board_text: str) -> tuple[bool, str]:
    """Split to a temp dir, reassemble, and compare. The migration's whole proof."""
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        names = split(root, board_text, out=t)
        rebuilt = assemble(t)
    if rebuilt == board_text:
        return True, f"{len(names)} fragments reassemble byte-identically"
    a, b = board_text.split("\n"), rebuilt.split("\n")
    for i, (x, y) in enumerate(zip(a, b), start=1):
        if x != y:
            return False, (f"first difference at line {i}\n"
                           f"  original: {x[:100]!r}\n  rebuilt : {y[:100]!r}")
    return False, f"identical for {min(len(a), len(b))} lines, then lengths differ: {len(a)} vs {len(b)}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=None, help="repository root (default: the parent of tools/)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="exit 1 if BOARD.md is out of date")
    g.add_argument("--write", action="store_true", help="regenerate BOARD.md from the fragments")
    g.add_argument("--split", action="store_true", help="one-time migration: BOARD.md -> fragments")
    g.add_argument("--verify", action="store_true", help="split to a temp dir, reassemble, diff")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    board = root / "BOARD.md"

    try:
        if args.split or args.verify:
            text = board.read_text(encoding="utf-8")
            if args.verify:
                ok, note = verify(root, text)
                print(("ok: " if ok else "MISMATCH: ") + note)
                return 0 if ok else 1
            names = split(root, text)
            print(f"wrote {len(names)} fragments to {ENTRIES}/ and the preamble to {PREAMBLE}")
            print(f"  first: {names[0]}")
            print(f"  last : {names[-1]}")
            print("Now `--verify` proves the round trip, and `--check` keeps it true.")
            return 0

        rebuilt = assemble(root)
        current = board.read_text(encoding="utf-8") if board.exists() else None
        if args.check:
            if current == rebuilt:
                print(f"BOARD.md is up to date ({rebuilt.count(chr(10) + '### ') + rebuilt.startswith('### ')} entries assembled)")
                return 0
            print("BOARD.md does not match the fragments. Run --write.")
            if current is not None:
                a, b = current.split("\n"), rebuilt.split("\n")
                for i, (x, y) in enumerate(zip(a, b), start=1):
                    if x != y:
                        print(f"  first difference at line {i}")
                        print(f"    committed: {x[:90]!r}")
                        print(f"    assembled: {y[:90]!r}")
                        break
                else:
                    print(f"  identical for {min(len(a), len(b))} lines, then {len(a)} vs {len(b)}")
            return 1
        board.write_text(rebuilt, encoding="utf-8")
        print(f"wrote BOARD.md from {ENTRIES}/")
        return 0
    except ProbeFailure as exc:
        print(f"PROBE FAILED: {exc}")
        print("exit 2 — no reading taken. This is not an up-to-date board.")
        return 2
    except OSError as exc:
        print(f"PROBE FAILED: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
