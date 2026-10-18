#!/usr/bin/env python3
"""Tests for uptake.py. Each pins one rule a reader of the numbers relies on."""
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import uptake  # noqa: E402


def board(*entries):
    return "# Board\n\n" + "\n\n".join(f"### {s} UTC · {who} · note\n\n{body}" for s, who, body in entries) + "\n"


def cites(text):
    m = uptake.measure(uptake.parse(text))
    return [r["cites"] for r in m["entries"]], m


class Uptake(unittest.TestCase):
    def test_full_stamp_resolves(self):
        c, _ = cites(board(("2026-10-01 10:00", "a", "hello"),
                           ("2026-10-02 11:00", "b", "replying to 2026-10-01 10:00")))
        self.assertEqual(c, [[], [1]])

    def test_bare_time_resolves_to_nearest_earlier(self):
        c, _ = cites(board(("2026-10-01 10:00", "a", "x"),
                           ("2026-10-02 10:00", "b", "y"),
                           ("2026-10-02 12:00", "c", "to the 10:00 entry")))
        self.assertEqual(c[2], [2])        # yesterday's 10:00 is not the one meant

    def test_bare_time_outside_48h_is_not_guessed(self):
        c, _ = cites(board(("2026-10-01 10:00", "a", "x"),
                           ("2026-10-05 12:00", "c", "to the 10:00 entry")))
        self.assertEqual(c[1], [])

    def test_own_time_is_a_signature_not_a_citation(self):
        c, _ = cites(board(("2026-10-01 10:00", "a", "x"),
                           ("2026-10-02 10:00", "b", "posted at 10:00")))
        self.assertEqual(c[1], [])

    def test_only_earlier_positions_are_cited(self):
        # An entry appended later but stamped earlier (out of clock order) cannot be
        # cited by an entry above it: the board is append order, not clock order.
        c, _ = cites(board(("2026-10-01 10:00", "a", "mentions 09:30"),
                           ("2026-10-01 09:30", "late", "arrived late")))
        self.assertEqual(c, [[], []])

    def test_full_stamp_is_not_also_read_as_bare_time(self):
        c, _ = cites(board(("2026-10-01 10:00", "a", "x"),
                           ("2026-10-01 11:00", "b", "y"),
                           ("2026-10-01 12:00", "c", "see 2026-10-01 10:00")))
        self.assertEqual(c[2], [1])

    def test_orphans_and_reach(self):
        _, m = cites(board(("2026-10-01 10:00", "a", "x"),
                           ("2026-10-01 10:30", "b", "to 10:00"),
                           ("2026-10-01 13:00", "c", "also 10:00")))
        rows = m["entries"]
        self.assertEqual([r["uptake"] for r in rows], [2, 0, 0])
        self.assertEqual(rows[0]["lag_h"], 0.5)
        self.assertEqual(rows[0]["reach_h"], 3.0)

    def test_items_are_counted(self):
        _, m = cites(board(("2026-10-01 10:00", "a", "opened #208"),
                           ("2026-10-02 10:00", "b", "merged PR #208, see #12")))
        by = {r["item"]: r for r in m["items"]}
        self.assertEqual(by[208]["mentions"], 2)
        self.assertEqual(by[208]["reach_h"], 24.0)

    def test_empty_board_says_so_rather_than_measuring_zero(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "BOARD.md"
            p.write_text("# nothing\n", encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(uptake.main([str(p)]), 0)
            self.assertIn("not a measurement of zero", buf.getvalue())

    def test_real_board_parses(self):
        real = Path(__file__).resolve().parents[1] / "BOARD.md"
        if not real.exists():
            self.skipTest("no BOARD.md")
        m = uptake.measure(uptake.parse(real.read_text(encoding="utf-8")))
        self.assertGreaterEqual(len(m["entries"]), 1)


if __name__ == "__main__":
    unittest.main()
