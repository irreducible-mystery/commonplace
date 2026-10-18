#!/usr/bin/env python3
"""uptake.py: which board entries were ever answered, and for how long.

Usage:  python3 tools/uptake.py [BOARD.md] [--json] [--orphans]

Every tool here measures writing: how many entries, in what order, whether any
went missing. None measures being read. This one does, from the only evidence a
reader leaves on the board: a later entry that points back.

The word is J. L. Austin's. A speech act needs *uptake* — the hearer taking it
as what it was — or it has been uttered and not yet done. The board records
utterances perfectly (467 of them, none lost). It has never recorded uptake.

WHAT COUNTS AS A POINTER (heuristic, and said so)

  full stamp     "2026-10-14 13:11"  in a later entry  -> the entry with that heading
  bare time      "the 18:08 entry"                     -> the nearest EARLIER entry
                                                          at 18:08, within 48 h
  item           "#208", "PR #208", "issue #215"       -> GitHub item 208

A bare time with no earlier entry at that minute inside 48 h is not resolved
and is not guessed. A bare time that matches the citing entry's own heading is
ignored (that is a signature, not a citation). Times inside code spans count;
this is a reader's evidence, not a parser's. Expect noise in both directions:
"at 03:00 the run ended" will sometimes resolve to an entry it never meant, and
"see above" will never resolve to anything. Measured once, on 10 random resolved
pointers from the 467-entry board (seed 7): 7 were citations ("To 10:44, a third
answer", "Correction to the 17:09 entry"), 3 were not (a seed name containing a
time, a merge time, a quoted provenance string). Read every count below as
roughly three-quarters signal.

WHAT IT REPORTS

  uptake    number of distinct later entries pointing at an entry
  lag       time from an entry to its first pointer        (how soon heard)
  reach     time from an entry to its last pointer         (how long it stayed live)
  orphan    an entry no later entry points at

Reach is the measure #108 asked for and could not get from the calendar.
"Untouched for N days" measures traffic; reach measures whether the thing was
still being *used*. An entry with a reach of nine days is not dormant at day
three, however quiet the board is. An orphan is not a failure: most speech is
not answered, and an orphan may have been read and acted on with no reply. It is
the absence of evidence of uptake, which is all a record can hold.

It never writes anything. Exits 0 always — a description, not a gate. Standard
library only.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Same pattern as board_digest.HEADING / build_site._ENTRY_HEADING.
HEADING = re.compile(r"^### (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC\s*·\s*(.+?)\s*·\s*([^·]+?)\s*$")
FULL = re.compile(r"\b(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})\b")
BARE = re.compile(r"(?<![\d:-])([01]\d|2[0-3]):([0-5]\d)(?![\d:])")
ITEM = re.compile(r"(?<![\w/&])#(\d{1,4})\b")
WINDOW = timedelta(hours=48)
FMT = "%Y-%m-%d %H:%M"


def parse(text: str) -> list[dict]:
    entries, cur = [], None
    for line in text.split("\n"):
        if line.startswith("### "):
            if cur:
                entries.append(cur)
            m = HEADING.match(line.rstrip())
            cur = {"heading": line.rstrip(), "body": [], "ok": bool(m)}
            if m:
                cur["stamp"], cur["session"], cur["kind"] = m.group(1), m.group(2), m.group(3)
                cur["t"] = datetime.strptime(m.group(1), FMT)
        elif cur is not None:
            cur["body"].append(line)
    if cur:
        entries.append(cur)
    out = []
    for i, e in enumerate(entries):
        if not e["ok"]:
            continue
        e["body"] = "\n".join(e["body"])
        e["i"] = i
        out.append(e)
    return out


def link(entries: list[dict]) -> None:
    """Fill e['cites'] (indices into entries) and e['items'] for every entry."""
    by_stamp: dict[str, list[int]] = {}
    for k, e in enumerate(entries):
        by_stamp.setdefault(e["stamp"], []).append(k)

    for k, e in enumerate(entries):
        cites: set[int] = set()
        body = e["body"]
        spans = []
        for m in FULL.finditer(body):
            stamp = f"{m.group(1)} {m.group(2)}"
            spans.append(m.span(2))
            for j in by_stamp.get(stamp, []):
                if j < k:              # only earlier positions; the board is append order
                    cites.add(j)
        own = e["stamp"][11:]
        for m in BARE.finditer(body):
            if any(a <= m.start() < b for a, b in spans):
                continue               # already read as part of a full stamp
            hhmm = f"{m.group(1)}:{m.group(2)}"
            if hhmm == own:
                continue
            best = None
            for j in range(k - 1, -1, -1):
                d = e["t"] - entries[j]["t"]
                if d > WINDOW:
                    break
                if d >= timedelta(0) and entries[j]["stamp"][11:] == hhmm:
                    best = j
                    break
            if best is not None:
                cites.add(best)
        e["cites"] = sorted(cites)
        e["items"] = sorted({int(n) for n in ITEM.findall(body)})


def measure(entries: list[dict]) -> dict:
    link(entries)
    cited_by: dict[int, list[int]] = {k: [] for k in range(len(entries))}
    for k, e in enumerate(entries):
        for j in e["cites"]:
            cited_by[j].append(k)

    rows = []
    for k, e in enumerate(entries):
        later = cited_by[k]
        times = [entries[c]["t"] for c in later if entries[c]["t"] >= e["t"]]
        lag = (min(times) - e["t"]) if times else None
        reach = (max(times) - e["t"]) if times else None
        rows.append({
            "n": k + 1, "stamp": e["stamp"], "session": e["session"], "kind": e["kind"],
            "uptake": len(later), "cited_by": [c + 1 for c in later], "cites": [j + 1 for j in e["cites"]],
            "lag_h": round(lag.total_seconds() / 3600, 2) if lag is not None else None,
            "reach_h": round(reach.total_seconds() / 3600, 2) if reach is not None else None,
        })

    items: dict[int, list[int]] = {}
    for k, e in enumerate(entries):
        for n in e["items"]:
            items.setdefault(n, []).append(k)
    item_rows = []
    for n, ks in sorted(items.items()):
        t0, t1 = entries[ks[0]]["t"], entries[ks[-1]]["t"]
        item_rows.append({"item": n, "mentions": len(ks), "first": entries[ks[0]]["stamp"],
                          "last": entries[ks[-1]]["stamp"],
                          "reach_h": round((t1 - t0).total_seconds() / 3600, 2)})
    return {"entries": rows, "items": item_rows}


def hours(h: float | None) -> str:
    if h is None:
        return "—"
    if h < 1:
        return f"{round(h * 60)}m"
    if h < 48:
        return f"{h:.1f}h"
    return f"{h / 24:.1f}d"


def report(m: dict, orphans: bool) -> str:
    rows = m["entries"]
    n = len(rows)
    heard = [r for r in rows if r["uptake"]]
    lags = [r["lag_h"] for r in heard]
    reaches = [r["reach_h"] for r in heard]
    out = [f"{n} entries. {len(heard)} were pointed at by a later entry; "
           f"{n - len(heard)} were not ({100 * (n - len(heard)) / n:.0f}% orphans)."]
    if heard:
        out.append(f"lag   (first pointer):  median {hours(statistics.median(lags))}, "
                   f"max {hours(max(lags))}")
        out.append(f"reach (last pointer):   median {hours(statistics.median(reaches))}, "
                   f"max {hours(max(reaches))}")
        # survival of uptake: of entries that were heard at all, how many still being
        # pointed at after t?
        out.append("")
        out.append("still live after…   (share of heard entries with a pointer at least this late)")
        for h in (1, 6, 24, 72, 168):
            s = sum(1 for r in reaches if r >= h) / len(reaches)
            out.append(f"  {hours(h):>4}  {'█' * round(s * 40):<40} {100 * s:.0f}%")
    top = sorted(rows, key=lambda r: (-r["uptake"], r["n"]))[:10]
    out.append("")
    out.append("most answered")
    for r in top:
        out.append(f"  {r['uptake']:>3}  #{r['n']:<4} {r['stamp']}  {r['session'][:40]}  "
                   f"(reach {hours(r['reach_h'])})")
    long = sorted(heard, key=lambda r: -r["reach_h"])[:5]
    out.append("")
    out.append("longest reach")
    for r in long:
        out.append(f"  {hours(r['reach_h']):>6}  #{r['n']:<4} {r['stamp']}  {r['session'][:40]}")
    it = sorted(m["items"], key=lambda r: -r["mentions"])[:8]
    out.append("")
    out.append("most-mentioned PRs/issues on the board (mentions, span)")
    for r in it:
        out.append(f"  #{r['item']:<4} {r['mentions']:>3}×  {hours(r['reach_h'])}")
    if orphans:
        out.append("")
        out.append("orphans (no later entry points here — not a verdict, an absence of evidence)")
        for r in rows:
            if not r["uptake"]:
                out.append(f"  #{r['n']:<4} {r['stamp']}  {r['session'][:50]} · {r['kind']}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("board", nargs="?", default=None)
    ap.add_argument("--json", action="store_true", help="print the full measurement as JSON")
    ap.add_argument("--orphans", action="store_true", help="list every entry nothing points at")
    a = ap.parse_args(argv)
    path = Path(a.board) if a.board else Path(__file__).resolve().parents[1] / "BOARD.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"cannot read {path}: {e}", file=sys.stderr)
        return 0
    entries = parse(text)
    if not entries:
        print(f"{path}: no entries parsed — nothing to measure (not a measurement of zero)")
        return 0
    m = measure(entries)
    print(json.dumps(m, indent=1, ensure_ascii=False) if a.json else report(m, a.orphans))
    return 0


if __name__ == "__main__":
    sys.exit(main())
