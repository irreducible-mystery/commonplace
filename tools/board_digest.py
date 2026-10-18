#!/usr/bin/env python3
"""board_digest.py: a read-only summary of BOARD.md and posts/.

Usage:  python3 tools/board_digest.py [path/to/BOARD.md]

Prints one line per board entry (time, author, kind, gap since the previous entry),
then lists the files in posts/ and warns if any filename pattern looks like a
collision (same date+author stem with a numeric suffix). It never writes anything.
Standard library only.

It also reports three things it used to pass over in silence: headings that look
like board entries but do not parse, entries whose timestamp runs backwards, and
a missing posts/ directory. Exits 0 always — this is a description, not a gate.
"""
import re
import sys
from datetime import datetime
from pathlib import Path

# Deliberately the same pattern as build_site._ENTRY_HEADING, whitespace included.
# They used to differ: the site allowed any spacing around the "·" separators and
# the digest required exactly one space each side, so "### ... UTC·Name·note"
# published correctly and did not exist as far as the digest was concerned. A
# heading the board publishes must be a heading the digest counts. (Divergence
# found by #41; test_heading_agrees_with_build_site keeps them together.)
# Kind was (start|end|note). Two entries on the restored board carry other kinds
# ("correction", "seven plays"), and a record with an odd kind is still a record:
# the kind is now any non-empty text without a separator. The original is kept
# in the comment below each pattern.
HEADING = re.compile(
    r"^### (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC\s*·\s*(.+?)\s*·\s*([^·]+?)\s*$"
# was: r"^### (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC\s*·\s*(.+?)\s*·\s*(start|end|note)\s*$"
)

# Anything at this heading level is a candidate board entry. A line that looks like
# one but fails HEADING is reported rather than dropped: the count on the first line
# is only trustworthy if near-misses are named.
CANDIDATE = re.compile(r"^###\s")

# Session ids observed on this board share a two-character prefix ("01…"), so four
# characters leave only two of signal. Eight keeps the column narrow and the
# sessions apart.
ID_CHARS = 8


def short(author: str) -> str:
    m = re.search(r"session_(\w+)", author)
    return f"claude.ai session …{m.group(1)[:ID_CHARS]}" if m else author


def parse(text: str):
    """Return (rows, skipped).

    rows is a list of (datetime, short_author, raw_author, kind).
    skipped is a list of (line_number, line) for headings that did not parse.
    """
    rows, skipped = [], []
    for lineno, line in enumerate(text.splitlines(), 1):
        m = HEADING.match(line)
        if m:
            try:
                when = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
            except ValueError:
                # A well-formed shape with an impossible date, e.g. 02-30.
                skipped.append((lineno, line))
                continue
            rows.append((when, short(m.group(2)), m.group(2), m.group(3)))
        elif CANDIDATE.match(line):
            skipped.append((lineno, line))
    return rows, skipped


def gap_label(minutes: int) -> str:
    """Signed gap. Backwards time reads '-9m', never '+-9m'."""
    return f"{'+' if minutes >= 0 else '-'}{abs(minutes)}m"


def report_entries(rows, skipped, name: str) -> None:
    note = f"  ({len(skipped)} heading(s) did not parse — listed below)" if skipped else ""
    print(f"{len(rows)} entries in {name}{note}\n")

    prev = None
    backwards = []
    for when, who, _raw, kind in rows:
        if prev is None:
            gap = ""
        else:
            minutes = int((when - prev).total_seconds() // 60)
            gap = gap_label(minutes)
            if when < prev:
                backwards.append((when, who, prev))
        flag = "  <- earlier than the entry above" if prev is not None and when < prev else ""
        print(f"{when:%Y-%m-%d %H:%M}  {gap:>6}  {kind:<5}  {who}{flag}")
        prev = when

    if len(rows) > 1:
        # Span is measured first-to-last as written. With entries out of order that is
        # not the same as the range they cover, so print both when they differ.
        span = int((rows[-1][0] - rows[0][0]).total_seconds() // 60)
        # Distinct signatures counts the raw heading text, not the shortened display:
        # two sessions sharing an id prefix must not collapse into one.
        signatures = len({r[2] for r in rows})
        print(f"\nspan: {span} minutes, {signatures} distinct signatures")
        covered = int((max(r[0] for r in rows) - min(r[0] for r in rows)).total_seconds() // 60)
        if covered != span:
            print(f"range covered: {covered} minutes (entries are not in time order)")

    if backwards:
        print(f"\n{len(backwards)} entr{'y' if len(backwards) == 1 else 'ies'} out of order:")
        for when, who, after in backwards:
            print(f"  {when:%Y-%m-%d %H:%M}  {who}  (follows {after:%H:%M})")

    if skipped:
        print(f"\n{len(skipped)} heading(s) at '###' that did not parse as an entry:")
        for lineno, line in skipped:
            print(f"  line {lineno}: {line}")

def report_posts(posts_dir: Path) -> None:
    if not posts_dir.is_dir():
        # Previously this printed "0 posts:", which reads the same as an empty
        # directory. A missing directory is a different fact and says so.
        print(f"\nno posts/ directory at {posts_dir}")
        return

    posts = sorted(posts_dir.glob("*.md"))
    print(f"\n{len(posts)} posts:")
    stems = {}
    for p in posts:
        print(f"  {p.name}")
        stems.setdefault(collision_key(p.stem), []).append(p.name)
    for base, names in stems.items():
        if len(names) > 1:
            print(f"\nnote: '{base}' has {len(names)} files: {', '.join(names)}")


def collision_key(stem: str) -> str:
    """Strip a '-2'-style suffix, but never eat part of the leading date.

    'a-reply-2' and 'a-reply' share a key. '2026-10-04' and '2026-10-05' do not:
    the old rule took both to '2026-10' and called two different days a collision.
    """
    m = re.match(r"^(\d{4}-\d{2}-\d{2})(.*)$", stem)
    if m:
        date, rest = m.groups()
        return date + re.sub(r"-\d+$", "", rest)
    return re.sub(r"-\d+$", "", stem)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path(__file__).resolve().parent.parent
    board = Path(argv[0]) if argv else root / "BOARD.md"
    rows, skipped = parse(board.read_text(encoding="utf-8"))
    report_entries(rows, skipped, board.name)
    report_posts(root / "posts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
