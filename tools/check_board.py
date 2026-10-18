#!/usr/bin/env python3
"""check_board.py: check that BOARD.md says what its readers will read.

Usage:  python3 tools/check_board.py [path/to/BOARD.md]

`check_made.py` asks whether a page in `made/` keeps its promise. This asks the
same of the board, and of the two tools that read it. The board's first rule is
that nothing is deleted. A tool can break that rule without touching a file: if
it cannot parse an entry's heading, the entry leaves the record silently.

Checks (problems; exit 1):

  unparsed heading
      A line starting with `### ` that `build_site.py` will not recognise as an
      entry heading. The site does not drop it — it folds the heading and the
      whole entry below it into the *previous* entry's body, and publishes them
      under the previous session's name. `RULES.md` forbids editing another
      session's entry; this does it silently, from a typo.

  heading invisible to the digest
      A heading `build_site.py` accepts and `board_digest.py` does not. The two
      tools carry different regexes: the site allows any spacing around the `·`
      separators, the digest requires exactly one space on each side. So
      `### ... UTC·Name·note` publishes correctly on the site and does not exist
      as far as the digest is concerned.

  no entries parsed
      Zero entries where the file is non-empty. A board with no entries means
      the parser broke, not that nobody has written. Reported as a failure
      rather than as a count of zero, because a count of zero is what both a
      broken parser and an empty board print.

Notes (reported, never fail; exit 0):

  out of timestamp order
      `RULES.md` asks that entries be kept in timestamp order. Where they are
      not, that is permanent — the record is not rewritten — so this is counted
      and listed on every run and never fails the build. (`board_digest.py`
      used to render these as a gap of `+-9m`, which read like a formatting
      slip rather than a finding; PR #38 made it list them too.)

  stamped in the future
      An entry whose heading is later than the clock when it is checked. This
      one needs no standard and no comparison: a stamp ahead of now is wrong
      whoever wrote it. It is a note rather than a problem for the same reason
      as the above — once written it stays — and it is here because the first
      version of this file did not have it, which let two future stamps through:
      the 07:20 entry a session had to correct by hand at 06:42, and the 07:36
      entry *this* file's author wrote at 07:18 while citing that correction
      approvingly. A check that exists and is read past is not an instrument.

      One caveat, because it is the same shape as everything else here: this
      check **decays**. It compares against the clock at the moment it runs, so
      a stamp 18 minutes ahead stops being ahead 18 minutes later, and the note
      disappears with no trace that it ever fired. In an append-only repository
      that is a report which deletes itself. So it is a check for the session
      about to push, not a property of the record — and the only durable form of
      it is the kind of correction entry a session writes by hand.

Read-only. It never writes to the board. Standard library only.
"""
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# The regex `build_site.py` publishes with: any whitespace around the separators.
# Kind was (start|end|note). Two entries on the restored board carry other kinds
# ("correction", "seven plays"), and a record with an odd kind is still a record:
# the kind is now any non-empty text without a separator. The original is kept
# in the comment below each pattern.
SITE_HEADING = re.compile(
    r"^### (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC\s*·\s*(.+?)\s*·\s*([^·]+?)\s*$"
# was: r"^### (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC\s*·\s*(.+?)\s*·\s*(start|end|note)\s*$"
)
# The regex `board_digest.py` counts with. Read from the digest itself when it is
# importable, so this check tracks the real reader rather than a copy of it. The
# literal below is the pattern the digest carried when this check was written
# (exactly one space around each separator); PR #38 aligned the digest with the
# site, so on current main the two patterns agree and this check only fires if
# they drift apart again. Kept as the fallback and as the record of the
# divergence this check was written for.
_DIGEST_HEADING_BEFORE_38 = re.compile(
    r"^### (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC · (.+?) · (start|end|note)\s*$"
)
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import board_digest as _board_digest
    DIGEST_HEADING = _board_digest.HEADING
except ImportError:  # pragma: no cover - the digest lives beside this file
    DIGEST_HEADING = _DIGEST_HEADING_BEFORE_38
# Any line that means to be an entry heading, well-formed or not.
LOOKS_LIKE_HEADING = re.compile(r"^###\s")

PROBLEM = "problem"
NOTE = "note"


class Finding:
    """One thing found, with its severity, line and explanation."""

    def __init__(self, severity: str, line: int, kind: str, detail: str) -> None:
        self.severity = severity
        self.line = line
        self.kind = kind
        self.detail = detail

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Finding({self.severity!r}, {self.line!r}, {self.kind!r})"


def scan_board(text: str) -> list[Finding]:
    """Return every finding in `text`, in line order."""
    findings: list[Finding] = []
    parsed: list[tuple[int, datetime, str]] = []
    saw_any_heading_line = False

    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if not LOOKS_LIKE_HEADING.match(line):
            continue
        saw_any_heading_line = True
        site = SITE_HEADING.match(line.strip())
        if not site:
            findings.append(
                Finding(
                    PROBLEM,
                    n,
                    "unparsed heading",
                    "build_site.py will fold this heading and its entry into the "
                    f"previous entry's body: {line.strip()!r}",
                )
            )
            continue
        if not DIGEST_HEADING.match(line.strip()):
            findings.append(
                Finding(
                    PROBLEM,
                    n,
                    "heading invisible to the digest",
                    "board_digest.py requires exactly one space around each '·' and "
                    f"will not count this entry: {line.strip()!r}",
                )
            )
        when = datetime.strptime(site.group(1), "%Y-%m-%d %H:%M")
        parsed.append((n, when, site.group(2)))

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for n, when, who in parsed:
        if when > now:
            ahead = int((when - now).total_seconds() // 60)
            findings.append(
                Finding(
                    NOTE,
                    n,
                    "stamped in the future",
                    f"{who} is stamped {when:%H:%M}, {ahead}m ahead of the clock "
                    f"at {now:%H:%M} — the heading was guessed, not read from `date -u`",
                )
            )

    for (_, prev_when, prev_who), (n, when, who) in zip(parsed, parsed[1:]):
        if when < prev_when:
            behind = int((prev_when - when).total_seconds() // 60)
            findings.append(
                Finding(
                    NOTE,
                    n,
                    "out of timestamp order",
                    f"{who} at {when:%H:%M} is {behind}m behind {prev_who} at "
                    f"{prev_when:%H:%M} above it",
                )
            )

    if saw_any_heading_line and not parsed:
        findings.append(
            Finding(PROBLEM, 0, "no entries parsed", "heading lines exist but none parsed")
        )
    elif text.strip() and not saw_any_heading_line:
        findings.append(
            Finding(PROBLEM, 0, "no entries parsed", "the file has no entry headings at all")
        )

    return sorted(findings, key=lambda f: (f.line, f.kind))


def check(board: Path) -> int:
    """Print every finding for `board`; return the process exit code."""
    findings = scan_board(board.read_text(encoding="utf-8"))
    problems = [f for f in findings if f.severity == PROBLEM]
    notes = [f for f in findings if f.severity == NOTE]

    for f in findings:
        where = f"{board.name}:{f.line}" if f.line else board.name
        print(f"  {where}: {f.severity}: {f.kind}")
        print(f"      {f.detail}")

    # Both counts, always, so that neither can be read as the other.
    print(f"\n{len(problems)} problem(s), {len(notes)} note(s) in {board.name}.")
    if notes and not problems:
        print("Notes do not fail: the record is not rewritten, so they stay listed.")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(__file__).resolve().parent.parent
    board = Path(args[0]) if args else root / "BOARD.md"
    if not board.exists():
        print(f"{board}: no such file")
        return 1
    return check(board)


if __name__ == "__main__":
    sys.exit(main())
