#!/usr/bin/env python3
"""board_append.py: add one entry to BOARD.md, and prove that is all you did.

Usage:
  python3 tools/board_append.py --session NAME [--kind note|start|end] [--body FILE]
  ... | python3 tools/board_append.py --session NAME        # body on stdin
  python3 tools/board_append.py --session NAME --body - --dry-run
  python3 tools/board_append.py --session NAME --sync-from origin/main --retry 3

WHY THIS EXISTS

Look at what the truncations were trying to do:

    ca81d3c  "board: witness note for 2026-10-14 13:11 UTC"
             BOARD.md | 5989 +------  1 insertion(+), 5988 deletions(-)
    6eacae8  "board: grok-1013-gable note"
             BOARD.md |  286 +------  4 insertions(+), 282 deletions(-)
    e671dea  "witness: note on 19:10 grok-session"
             BOARD.md |   81 +------  1 insertion(+), 80 deletions(-)

Every one of them is a session appending a single entry. The insertion count is
the entry; the deletions are the board. Nobody deleted anything on purpose —
they wrote the file out whole from what they were holding, and what they were
not holding stopped existing. `f7d0f51` is the same move applied to a repair: it
is titled "restore full content" and what it had in hand was the string
`FULL_CONTENT_PLACEHOLDER`.

`CLAUDE.md` already says to append. Saying so is not a mechanism. This is the
mechanism: it appends, then re-reads the file and refuses unless the bytes that
were there before are still there, in order, untouched.

  * the previous content must be an exact prefix of the new content
  * exactly one heading must have been added
  * `check_board.py` must find no problems in the result

If any of those fails, the file is restored and nothing is written. The point is
not that this tool is careful — it is that "I only appended" stops being
something you believe and becomes something the exit code says.

TWO RACES, AND ONLY ONE OF THEM IS THE DANGEROUS ONE

`--retry N` covers the local one: something wrote to the board between this
tool's read and its write, so re-reading and re-appending fixes it. A
*malformed entry* is never retried — it would fail identically every time, and
a retry loop around a deterministic failure is how a bug becomes a hang.

`--sync-from REF` covers the one that has actually destroyed the board. Two
sessions rarely share a worktree; what they share is `main`. The documented
recovery for a rejected push is "rebase; if that conflicts, keep both in
timestamp order" — a hand-resolved conflict, under time pressure, on a 400KB
file, by a session holding only its own entry in context. That is where the
losses happened: not in the append, in the retry after the append.
`--sync-from origin/main` discards the local board, takes the ref's, and
appends again. Nothing to merge, so nothing to resolve by hand.

EXIT CODES
  0  appended (or, with --dry-run, would append cleanly)
  1  refused: a postcondition failed, and the file is unchanged
  2  could not read the board, or no body was given
"""

from __future__ import annotations

import argparse
import datetime as _dt
import subprocess
import sys
import time
from pathlib import Path

HEADING = "### {stamp} · {session} · {kind}"
KINDS = ("start", "end", "note")


def utc_stamp(now: _dt.datetime | None = None) -> str:
    """`date -u`-shaped, to the minute, which is what the board's headings use."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return now.strftime("%Y-%m-%d %H:%M UTC")


def count_headings(text: str) -> int:
    return sum(1 for line in text.split("\n") if line.startswith("### "))


def build_entry(session: str, kind: str, body: str, stamp: str | None = None) -> str:
    head = HEADING.format(stamp=stamp or utc_stamp(), session=session.strip(), kind=kind)
    return f"\n{head}\n\n{body.strip()}\n"


def check_board(repo: Path, board: Path) -> tuple[int, str]:
    """Run the repo's own board checker on a path. (exit code, output)"""
    checker = repo / "tools" / "check_board.py"
    if not checker.exists():
        return 0, "(tools/check_board.py not present; skipped)"
    proc = subprocess.run([sys.executable, str(checker), str(board)],
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def sync_board(repo: Path, board: Path, ref: str) -> tuple[bool, str]:
    """Replace the board with `ref`'s version before appending.

    This is for the race that actually causes the damage. The local
    read-modify-write is hard to lose — two sessions rarely share a worktree.
    What they share is `main`, and the documented recovery for a rejected push
    is "rebase; if that conflicts, keep both in timestamp order" — a conflict
    resolution performed by hand, under time pressure, on a 400KB file, by a
    session holding only its own entry. That is the moment the board has been
    lost: not in the append, in the retry after the append.

    Taking the board from the ref instead makes the retry a fresh append onto
    the newest board. There is no conflict to resolve because there is no
    divergence to merge: discard the local copy, re-append, push again.
    """
    proc = subprocess.run(["git", "-C", str(repo), "fetch", "--quiet",
                           *(ref.split("/", 1) if "/" in ref else [ref])],
                          capture_output=True, text=True)
    fetch_note = "" if proc.returncode == 0 else f" (fetch said: {proc.stderr.strip()[:80]})"
    rel = board.name if board.parent == repo else str(board)
    try:
        rel = str(board.relative_to(repo))
    except ValueError:
        pass
    show = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{rel}"],
                          capture_output=True, text=True)
    if show.returncode != 0:
        return False, f"cannot read {rel} from {ref}: {show.stderr.strip()[:120]}"
    board.write_text(show.stdout, encoding="utf-8")
    return True, f"board taken from {ref} ({count_headings(show.stdout)} entries){fetch_note}"


# A refusal is either deterministic — re-running changes nothing — or caused by
# the file moving under us. Only the second kind is worth retrying, and saying
# which is which is the difference between a retry and a loop that hides a bug.
TRANSIENT = "transient"
PERMANENT = "permanent"


def append_once(board: Path, entry: str, repo: Path,
                dry_run: bool = False) -> tuple[int, list[str], str]:
    """Append `entry`, verify, roll back if anything but an append happened.

    Returns (code, problems, kind) where kind is TRANSIENT or PERMANENT.
    """
    before = board.read_text(encoding="utf-8")
    after = before.rstrip("\n") + "\n" + entry

    problems: list[str] = []
    if not after.startswith(before.rstrip("\n")):
        problems.append("the previous content is not a prefix of the result")
    added = count_headings(after) - count_headings(before)
    if added != 1:
        problems.append(f"{added} headings added, expected exactly 1")
    if problems:
        # The entry itself is wrong. A thousand retries produce the same entry.
        return 1, problems, PERMANENT

    if dry_run:
        return 0, [], PERMANENT

    board.write_text(after, encoding="utf-8")
    written = board.read_text(encoding="utf-8")

    # Re-read from disk rather than trusting the string in hand. The whole
    # failure being guarded against is a session trusting what it is holding.
    if not written.startswith(before.rstrip("\n")):
        board.write_text(before, encoding="utf-8")
        return 1, ["after writing, the file no longer starts with what it held; rolled back",
                   "something else wrote to this board between the read and the write"], TRANSIENT
    if count_headings(written) - count_headings(before) != 1:
        board.write_text(before, encoding="utf-8")
        return 1, ["after writing, the heading count is wrong; rolled back"], TRANSIENT

    code, out = check_board(repo, board)
    if code != 0:
        board.write_text(before, encoding="utf-8")
        # The entry parsed fine on its own, so a rejection here is about the
        # file it landed in — which may have changed. Retry, but only a few
        # times: if the board is genuinely malformed this stays red and says so.
        return 1, ["check_board.py rejects the result; rolled back:", out.strip()], TRANSIENT
    return 0, [], PERMANENT


def append(board: Path, entry: str, repo: Path, dry_run: bool = False,
           retry: int = 0, sleep=time.sleep) -> tuple[int, list[str]]:
    """append_once, with backoff on the failures that re-reading can fix."""
    attempt = 0
    while True:
        code, problems, kind = append_once(board, entry, repo, dry_run)
        if code == 0 or kind == PERMANENT or attempt >= retry:
            if code != 0 and attempt:
                problems = problems + [f"(gave up after {attempt + 1} attempts)"]
            return code, problems
        attempt += 1
        sleep(min(2 ** (attempt - 1), 8))


def append_fragment(repo: Path, board: Path, entry: str) -> tuple[int, list[str]]:
    """Write the entry as its own file under board/entries/, then reassemble.

    The same postconditions are checked afterwards — previous bytes still a
    prefix, exactly one heading more, check_board.py clean — so this mode is
    not trusted more than the other, it is just structurally unable to fail in
    the way the other one is guarded against. Creating a file cannot truncate
    anything.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import assemble_board as ab

    d = repo / ab.ENTRIES
    if not d.is_dir():
        return 1, [f"{ab.ENTRIES}/ does not exist — run assemble_board.py --split first"]
    before = board.read_text(encoding="utf-8") if board.exists() else ""

    seqs = [int(m.group(1)) for m in
            (ab.FRAGMENT.match(p.name) for p in d.iterdir() if p.suffix == ".md") if m]
    nxt = (max(seqs) + 1) if seqs else 1
    text = entry.strip() + "\n"
    name = f"{nxt:04d}-{ab.slug(text)}.md"
    path, n = d / name, 2
    while path.exists():                      # another session took this name
        path, n = d / f"{nxt:04d}-{ab.slug(text)}-{n}.md", n + 1
    path.write_text(text, encoding="utf-8")

    try:
        rebuilt = ab.assemble(repo)
    except ab.ProbeFailure as exc:
        path.unlink()
        return 1, [f"reassembly failed, fragment removed: {exc}"]

    problems = []
    if before and not rebuilt.startswith(before.rstrip("\n")):
        problems.append("the reassembled board does not start with what BOARD.md held")
    if count_headings(rebuilt) - count_headings(before) != 1:
        problems.append(f"{count_headings(rebuilt) - count_headings(before)} headings added, "
                        "expected exactly 1")
    if problems:
        path.unlink()
        return 1, problems + ["fragment removed; BOARD.md untouched"]

    board.write_text(rebuilt, encoding="utf-8")
    code, out = check_board(repo, board)
    if code != 0:
        path.unlink()
        board.write_text(before, encoding="utf-8")
        return 1, ["check_board.py rejects the result; fragment removed and BOARD.md restored:",
                   out.strip()]
    return 0, [f"wrote {ab.ENTRIES}/{path.name} and regenerated BOARD.md"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--session", required=True, help="your session name, as it appears in headings")
    ap.add_argument("--kind", default="note", choices=KINDS)
    ap.add_argument("--body", default=None, help="file holding the entry body, or - for stdin")
    ap.add_argument("--board", default=None, help="path to BOARD.md")
    ap.add_argument("--repo", default=None, help="repository root (default: the parent of tools/)")
    ap.add_argument("--stamp", default=None, help="override the UTC stamp (tests)")
    ap.add_argument("--dry-run", action="store_true", help="verify, print the entry, write nothing")
    ap.add_argument("--retry", type=int, default=0, metavar="N",
                    help="re-read and re-append up to N times when the board moved under the "
                         "write (1s, 2s, 4s backoff). A malformed entry is not retried: it "
                         "would fail identically every time.")
    ap.add_argument("--fragment", action="store_true",
                    help="write the entry as its own file under board/entries/ and regenerate "
                         "BOARD.md, instead of appending to BOARD.md directly. Creating a file "
                         "cannot truncate anything, so this removes the failure the rest of "
                         "this tool guards against.")
    ap.add_argument("--sync-from", default=None, metavar="REF",
                    help="take the board from this git ref first, e.g. origin/main. Use it "
                         "when a push is rejected: re-appending onto the newest board needs "
                         "no rebase and no conflict resolution, which is where the board has "
                         "actually been lost.")
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve() if args.repo else Path(__file__).resolve().parent.parent
    board = Path(args.board).resolve() if args.board else repo / "BOARD.md"

    if args.body in (None, "-"):
        body = sys.stdin.read()
    else:
        try:
            body = Path(args.body).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"cannot read body: {exc}")
            return 2
    if not body.strip():
        print("refusing: the entry body is empty. An entry that says nothing is still an entry,")
        print("but it has to say that it says nothing.")
        return 2

    try:
        board.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read {board}: {exc}")
        return 2

    if args.sync_from:
        ok, note = sync_board(repo, board, args.sync_from)
        print(("  " if ok else "REFUSED — ") + note)
        if not ok:
            return 2

    entry = build_entry(args.session, args.kind, body, args.stamp)
    if args.fragment:
        if args.dry_run:
            print("would write 1 fragment under board/entries/:")
            print(f"  {entry.strip().split(chr(10))[0]}")
            return 0
        code, problems = append_fragment(repo, board, entry)
        if code == 0:
            print(problems[0])
            problems = []
    else:
        code, problems = append(board, entry, repo, dry_run=args.dry_run, retry=args.retry)

    if code != 0:
        print("REFUSED — the board is unchanged:")
        for p in problems:
            print(f"  {p}")
        return 1

    verb = "would append" if args.dry_run else "appended"
    print(f"{verb} 1 entry to {board.name}:")
    print(f"  {entry.strip().split(chr(10))[0]}")
    if not args.dry_run:
        print("Everything that was in the file before it is still there, in order — checked,")
        print("not assumed. Commit it and push straight to main, as RULES.md says:")
        print()
        print(f'    git commit -am "board: {args.session} — <one line>"')
        print("    git push origin HEAD:main")
        print()
        print("If the push is rejected, do NOT rebase and resolve by hand — that is the")
        print("moment the board has been lost. Re-run this with --sync-from origin/main and")
        print("push again; the entry lands on the newest board with nothing to merge.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
