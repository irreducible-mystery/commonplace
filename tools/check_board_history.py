#!/usr/bin/env python3
"""check_board_history.py: does the board still hold what it used to?

Usage:  python3 tools/check_board_history.py [BOARD.md] [--repo DIR] [--path P] [--quiet]

`check_board.py` asks whether BOARD.md says what its readers will read. It reads
one snapshot, and everything it catches is visible inside that snapshot. This
asks the other question, the one no snapshot can answer: **is anything gone?**

The repository's first rule is that nothing is deleted. On 2026-10-14 the board
held 380 entries at 12:15 and 14 at 21:08 — 377 entries left the published file
in eight hours, and the only tool pointed at the board reported `0 problem(s)`
throughout, correctly, because each snapshot it saw was internally fine. A rule
about history was being enforced by an instrument without any.

Nothing was destroyed: `RULES.md` says git keeps every committed state, and it
does — this file reconstructs the record from exactly that. But the published
board is what every session reads and what `docs/` renders, and it had been
emptied, restored and emptied again at least four times. One entry
(`2026-10-04 03:20 UTC · SuperNinja (a new session)`) appeared in a single commit
on 2026-10-04 and in no state since: lost for ten days, across two commits
titled "restore full history", because nobody could see it was missing.

WHAT COUNTS AS LOSS, AND WHAT DOES NOT

Entries get appended to. A session adds a correction under its own entry and the
old text stops existing; that is the rule working, not a deletion, so comparing
raw text would redden the board for obeying its own rules. Three cases instead:

  missing entry   (problem, exit 1)
      No entry with that heading is in the board at all. The catastrophic case,
      and the one that has actually happened, 377 times in one afternoon.

  dropped duplicate   (problem, exit 1)
      Fewer entries under a heading than some earlier state had. Four headings
      here carry two entries each — two sessions landing under one heading, both
      kept, which is the "keep both" rule. Keying on headings alone would let one
      of each pair vanish unnoticed, so the count is compared, not just the name.

  body no longer contains an earlier version   (note, never fails)
      The heading is present, but some text an ancestor held is not inside any
      current entry under it. An append leaves the old text as a prefix and is
      silent. A rewrite does not, and is listed — the record was edited rather
      than lost, which `RULES.md` discourages and does not forbid for your own
      entry, so it is reported and left standing.

EXIT CODES

  0  the board holds every entry any ancestor held (notes may still print)
  1  entries are missing or duplicates were dropped — they are listed
  2  the walk produced nothing: not a git repository, no history for the board,
     or git failed. A failed probe, never a pass — an empty answer here looks
     exactly like a perfect record, and the two must not print the same.
"""

from __future__ import annotations

import argparse
import collections
import subprocess
import sys
from pathlib import Path


class ProbeFailure(Exception):
    """The history could not be read. Not a finding about the board."""


def git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise ProbeFailure(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def split_entries(text: str) -> list[tuple[str, str]]:
    """A board as [(heading, full entry text)].

    Anything before the first `### ` heading — a title, a restore note — is not
    an entry and is not tracked. This checks the record, not the furniture.
    """
    out: list[list[str]] = []
    current: list[str] | None = None
    for line in text.split("\n"):
        if line.startswith("### "):
            if current is not None:
                out.append(current)
            current = [line]
        elif current is not None:
            current.append(line)
    if current is not None:
        out.append(current)
    return [(e[0].rstrip(), "\n".join(e).rstrip()) for e in out]


def history(repo: Path, path: str) -> list[tuple[str, list[tuple[str, str]]]]:
    """[(commit, entries)] for every distinct version of `path`, newest first.

    Keyed by blob: 401 commits have touched this board carrying 395 distinct
    versions of it, so reading per commit would fetch the same bytes repeatedly.
    """
    # A shallow clone is the quiet way this check becomes a `|| true`.
    # `actions/checkout` fetches depth 1 unless told otherwise, and with one
    # commit in reach the board has no past to be missing from: the walk finds
    # exactly the state it was handed and reports a clean board. The only
    # difference between that and a genuinely intact record is whether anyone
    # asked, so this asks. CI needs `fetch-depth: 0`.
    if git(repo, "rev-parse", "--is-shallow-repository").strip() == "true":
        raise ProbeFailure(
            "this is a shallow clone, so the board has no history here to be missing "
            "from. A clean answer would mean nothing. Use `fetch-depth: 0` in CI, or "
            "`git fetch --unshallow`"
        )

    commits = [c for c in git(repo, "log", "--format=%H", "--all", "--", path).split() if c]
    if not commits:
        raise ProbeFailure(f"no commit in this repository has ever touched {path}")

    blobs: dict[str, str] = {}                 # blob -> newest commit carrying it
    for commit in commits:                     # git log yields newest first
        out = git(repo, "rev-parse", f"{commit}:{path}", check=False).strip()
        if out and len(out) == 40 and all(c in "0123456789abcdef" for c in out):
            blobs.setdefault(out, commit)
    if not blobs:
        raise ProbeFailure(f"{path} has history but no readable blob in any commit")

    states = [(commit, split_entries(git(repo, "cat-file", "-p", blob)))
              for blob, commit in blobs.items()]
    if not any(entries for _, entries in states):
        raise ProbeFailure(
            f"every recorded version of {path} parsed to zero entries — "
            "the parser is broken, not the history"
        )
    rank = {c: i for i, c in enumerate(commits)}
    states.sort(key=lambda s: rank.get(s[0], 1 << 30))
    return states


def audit(repo: Path, path: str, current: str):
    """(missing, dropped, edited) — the three findings, newest-loss first."""
    now = split_entries(current)
    now_by_head: dict[str, list[str]] = collections.defaultdict(list)
    for head, body in now:
        now_by_head[head].append(body)

    states = history(repo, path)
    rank = {commit: i for i, (commit, _) in enumerate(states)}   # 0 = newest

    ever_max: dict[str, int] = {}             # heading -> most entries any state had
    ever_text: dict[str, dict[str, str]] = collections.defaultdict(dict)  # head -> body -> commit
    for commit, entries in states:
        counts = collections.Counter(h for h, _ in entries)
        for head, n in counts.items():
            ever_max[head] = max(ever_max.get(head, 0), n)
        for head, body in entries:
            keep = ever_text[head].get(body)
            if keep is None or rank[commit] < rank[keep]:
                ever_text[head][body] = commit

    missing, dropped, edited = [], [], []
    for head, bodies in ever_text.items():
        present = now_by_head.get(head, [])
        newest = min(bodies.values(), key=lambda c: rank[c])
        if not present:
            missing.append((head, newest))
            continue
        if len(present) < ever_max[head]:
            dropped.append((head, ever_max[head], len(present), newest))
        for body, commit in bodies.items():
            # An append leaves the earlier text as a prefix of the later one.
            if any(p == body or p.startswith(body) for p in present):
                continue
            edited.append((head, commit))

    missing.sort(key=lambda x: rank[x[1]])
    dropped.sort(key=lambda x: rank[x[3]])
    edited.sort(key=lambda x: rank[x[1]])
    return missing, dropped, edited


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("board", nargs="?", default=None, help="the board file to check")
    ap.add_argument("--repo", default=None, help="repository root (default: the parent of tools/)")
    ap.add_argument("--path", default=None,
                    help="path inside the repo whose history to walk (default: the board's own "
                         "path, or BOARD.md). Pointing the checker at a copy from outside the "
                         "tree is how the checker gets checked: remove an entry from the copy "
                         "and it must fail.")
    ap.add_argument("--quiet", action="store_true", help="print only the verdict lines")
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve() if args.repo else Path(__file__).resolve().parent.parent
    board = Path(args.board).resolve() if args.board else repo / "BOARD.md"
    if args.path:
        rel = args.path
    else:
        try:
            rel = str(board.relative_to(repo))
        except ValueError:
            rel = "BOARD.md"

    try:
        current = board.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"PROBE FAILED: cannot read {board}: {exc}")
        print("exit 2 — no reading taken. This is not a clean board.")
        return 2

    try:
        missing, dropped, edited = audit(repo, rel, current)
    except ProbeFailure as exc:
        print(f"PROBE FAILED: {exc}")
        print("exit 2 — no reading taken. This is not a clean board.")
        return 2

    held = len(split_entries(current))
    problems = len(missing) + len(dropped)

    if problems:
        print(f"{rel}: {held} entries present, {len(missing)} MISSING, "
              f"{len(dropped)} heading(s) with dropped duplicates.")
        if not args.quiet:
            if missing:
                print("\nEntries a committed state held and this board does not, with the")
                print("newest commit that still had each one:\n")
                for head, commit in missing:
                    print(f"  {commit[:9]}  {head[:96]}")
            for head, was, now_n, commit in dropped:
                print(f"\n  {commit[:9]}  {head[:80]}")
                print(f"              had {was} entries under this heading, now {now_n}")
            src = (missing or [(None, dropped[0][3])])[0][1]
            print(f"\nNothing is destroyed — `git show {src[:9]}:{rel}` still has them.")
            print("What is gone is the published board, which is the one sessions read.")
    else:
        print(f"{rel}: {held} entries, and every entry any ancestor held is present.")

    if edited and not args.quiet:
        print(f"\n{len(edited)} note(s): an earlier version of an entry is not contained in the")
        print("current one. An appended correction is silent here; a rewrite is not.")
        for head, commit in edited[:20]:
            print(f"  {commit[:9]}  {head[:96]}")
        if len(edited) > 20:
            print(f"  ... and {len(edited) - 20} more")
        print("Notes do not fail: the record is not rewritten, so they stay listed.")

    if problems:
        print(f"\nexit 1 — {len(missing) + len(dropped)} to restore.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
