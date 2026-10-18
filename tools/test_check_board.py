"""Tests for tools/check_board.py. Run with: python3 -m unittest discover -s tools"""
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_board as cb  # noqa: E402

GOOD = "### 2026-10-04 01:00 UTC · Alice · note\n\nAlice's entry.\n"


def board(*chunks: str) -> str:
    return "# Board\n\nintro\n\n" + "\n".join(chunks)


def kinds(text: str) -> list[str]:
    return [f.kind for f in cb.scan_board(text)]


def severities(text: str) -> list[str]:
    return [f.severity for f in cb.scan_board(text)]


class WellFormedBoards(unittest.TestCase):
    def test_single_good_entry_is_clean(self):
        self.assertEqual(kinds(board(GOOD)), [])

    def test_entries_in_order_are_clean(self):
        self.assertEqual(
            kinds(
                board(
                    GOOD,
                    "### 2026-10-04 02:00 UTC · Bob · end\n\nBob's entry.\n",
                    "### 2026-10-04 02:00 UTC · Carol · start\n\nSame minute is fine.\n",
                )
            ),
            [],
        )

    def test_all_three_kinds_parse(self):
        for kind in ("start", "end", "note"):
            with self.subTest(kind=kind):
                self.assertEqual(kinds(board(f"### 2026-10-04 01:00 UTC · A · {kind}\n\nx\n")), [])

    def test_author_containing_a_separator_still_parses(self):
        # Real signatures include URLs and parentheses; only the two outer '·' count.
        text = board(
            "### 2026-10-04 01:00 UTC · Codex (maze postcard; checker review) · note\n\nx\n"
        )
        self.assertEqual(kinds(text), [])

    def test_hash_hash_hash_inside_prose_is_not_a_heading(self):
        # A '###' with no following space is not an entry heading and is left alone.
        self.assertEqual(kinds(board(GOOD, "###not-a-heading\n")), [])

    def test_body_text_mentioning_a_time_is_not_a_heading(self):
        self.assertEqual(kinds(board(GOOD, "\nTo 02:40 UTC · thanks for the note.\n")), [])


class UnparsedHeading(unittest.TestCase):
    """The silent drop: build_site.py folds these into the entry above."""

    def test_lowercase_utc_is_flagged(self):
        self.assertEqual(
            kinds(board(GOOD, "### 2026-10-04 02:00 utc · Bob · note\n\nBob's entry.\n")),
            ["unparsed heading"],
        )

    def test_missing_kind_is_flagged(self):
        self.assertEqual(
            kinds(board(GOOD, "### 2026-10-04 02:00 UTC · Bob\n\nBob's entry.\n")),
            ["unparsed heading"],
        )

    def test_unknown_kind_is_accepted(self):
        # was: test_unknown_kind_is_flagged, asserting ["unparsed heading"]. The restored
        # board (PR #195) carries two entries whose authors wrote other kinds
        # ("correction", "seven plays"); a record with an odd kind is still a record,
        # so the kind is now any text without a separator. A missing kind is still
        # flagged (test_missing_kind_is_flagged).
        self.assertEqual(
            kinds(board(GOOD, "### 2026-10-04 02:00 UTC · Bob · musing\n\nx\n")),
            [],
        )

    def test_hyphen_separator_is_flagged(self):
        self.assertEqual(
            kinds(board(GOOD, "### 2026-10-04 02:00 UTC - Bob - note\n\nx\n")),
            ["unparsed heading"],
        )

    def test_seconds_in_the_timestamp_are_flagged(self):
        self.assertEqual(
            kinds(board(GOOD, "### 2026-10-04 02:00:30 UTC · Bob · note\n\nx\n")),
            ["unparsed heading"],
        )

    def test_unparsed_heading_is_a_problem_not_a_note(self):
        self.assertEqual(
            severities(board(GOOD, "### 2026-10-04 02:00 utc · Bob · note\n\nx\n")),
            [cb.PROBLEM],
        )

    def test_the_detail_names_what_will_happen(self):
        found = cb.scan_board(board(GOOD, "### 2026-10-04 02:00 utc · Bob · note\n\nx\n"))
        self.assertIn("previous entry", found[0].detail)


class DigestInvisibleHeading(unittest.TestCase):
    """Accepted by build_site.py, dropped by board_digest.py.

    PR #38 aligned the digest's pattern with the site's, so on current main the
    two agree and this check is quiet. These tests pin the digest's pre-#38
    pattern so the check is still shown to fire when the readers differ; the
    last test shows it stays quiet with the digest as it now is.
    """

    def setUp(self):
        self._live = cb.DIGEST_HEADING
        cb.DIGEST_HEADING = cb._DIGEST_HEADING_BEFORE_38

    def tearDown(self):
        cb.DIGEST_HEADING = self._live

    def test_with_the_current_digest_the_tight_form_is_not_flagged(self):
        cb.DIGEST_HEADING = self._live
        self.assertEqual(kinds(board(GOOD, "### 2026-10-04 02:00 UTC·Bob·note\n\nx\n")), [])

    def test_no_spaces_around_separators_is_flagged(self):
        self.assertEqual(
            kinds(board(GOOD, "### 2026-10-04 02:00 UTC·Bob·note\n\nx\n")),
            ["heading invisible to the digest"],
        )

    def test_double_spaces_around_separators_is_flagged(self):
        self.assertEqual(
            kinds(board(GOOD, "### 2026-10-04 02:00 UTC  ·  Bob  ·  note\n\nx\n")),
            ["heading invisible to the digest"],
        )

    def test_one_space_each_side_is_the_form_both_tools_read(self):
        self.assertEqual(kinds(board(GOOD, "### 2026-10-04 02:00 UTC · Bob · note\n\nx\n")), [])

    def test_a_digest_invisible_entry_still_counts_for_ordering(self):
        # It parses for the site, so it takes part in the order check.
        text = board(
            "### 2026-10-04 03:00 UTC·Bob·note\n\nx\n",
            "### 2026-10-04 02:00 UTC · Carol · note\n\ny\n",
        )
        self.assertEqual(
            kinds(text), ["heading invisible to the digest", "out of timestamp order"]
        )


class TimestampOrder(unittest.TestCase):
    def test_an_entry_behind_the_one_above_is_a_note(self):
        text = board(
            "### 2026-10-04 03:00 UTC · Alice · note\n\nx\n",
            "### 2026-10-04 02:00 UTC · Bob · note\n\ny\n",
        )
        self.assertEqual(kinds(text), ["out of timestamp order"])
        self.assertEqual(severities(text), [cb.NOTE])

    def test_the_note_reports_how_far_behind(self):
        text = board(
            "### 2026-10-04 03:00 UTC · Alice · note\n\nx\n",
            "### 2026-10-04 02:00 UTC · Bob · note\n\ny\n",
        )
        self.assertIn("60m behind", cb.scan_board(text)[0].detail)

    def test_order_is_compared_to_the_entry_above_not_to_the_maximum(self):
        # One late entry, then back in order: exactly one note, not two.
        text = board(
            "### 2026-10-04 01:00 UTC · A · note\n\nx\n",
            "### 2026-10-04 05:00 UTC · B · note\n\nx\n",
            "### 2026-10-04 04:00 UTC · C · note\n\nx\n",
            "### 2026-10-04 06:00 UTC · D · note\n\nx\n",
        )
        self.assertEqual(kinds(text), ["out of timestamp order"])


class ZeroEntries(unittest.TestCase):
    def test_a_board_with_no_headings_is_a_problem(self):
        self.assertEqual(kinds("# Board\n\nJust a preamble.\n"), ["no entries parsed"])

    def test_headings_that_all_fail_to_parse_report_both_problems(self):
        found = kinds("# Board\n\n### 2026-10-04 02:00 utc · Bob · note\n\nx\n")
        self.assertEqual(found, ["no entries parsed", "unparsed heading"])

    def test_an_empty_file_is_not_reported_as_a_broken_parser(self):
        self.assertEqual(kinds(""), [])


class ExitCodes(unittest.TestCase):
    def run_check(self, text: str) -> tuple[int, str]:
        path = Path(tempfile.mkdtemp()) / "BOARD.md"
        path.write_text(text, encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            code = cb.check(path)
        return code, out.getvalue()

    def test_clean_board_exits_zero(self):
        code, out = self.run_check(board(GOOD))
        self.assertEqual(code, 0)
        self.assertIn("0 problem(s), 0 note(s)", out)

    def test_notes_alone_do_not_fail(self):
        code, out = self.run_check(
            board(
                "### 2026-10-04 03:00 UTC · A · note\n\nx\n",
                "### 2026-10-04 02:00 UTC · B · note\n\nx\n",
            )
        )
        self.assertEqual(code, 0)
        self.assertIn("1 note(s)", out)
        self.assertIn("Notes do not fail", out)

    def test_a_problem_exits_one(self):
        code, _ = self.run_check(board(GOOD, "### 2026-10-04 02:00 utc · B · note\n\nx\n"))
        self.assertEqual(code, 1)

    def test_both_counts_are_always_printed(self):
        # Neither count can be mistaken for the other.
        _, out = self.run_check(
            board(
                "### 2026-10-04 03:00 UTC · A · note\n\nx\n",
                "### 2026-10-04 02:00 utc · B · note\n\nx\n",
            )
        )
        self.assertIn("problem(s)", out)
        self.assertIn("note(s)", out)

    def test_a_missing_file_exits_one_without_a_traceback(self):
        missing = Path(tempfile.mkdtemp()) / "nope.md"
        out = io.StringIO()
        with redirect_stdout(out):
            code = cb.main([str(missing)])
        self.assertEqual(code, 1)
        self.assertIn("no such file", out.getvalue())


class TheRealBoard(unittest.TestCase):
    def test_the_board_in_this_repo_has_no_problems(self):
        real = Path(__file__).resolve().parent.parent / "BOARD.md"
        if not real.exists():  # pragma: no cover - the repo always has one
            self.skipTest("no BOARD.md")
        problems = [f for f in cb.scan_board(real.read_text(encoding="utf-8"))
                    if f.severity == cb.PROBLEM]
        self.assertEqual([f.kind for f in problems], [])


class WhatTheOtherToolsActuallyDo(unittest.TestCase):
    """Characterisation tests: the behaviour `check_board.py` exists to warn about.

    These record what `board_digest.py` and `build_site.py` do today with a
    heading they cannot parse. They are evidence for the checks above rather
    than a verdict on those tools. If either tool is changed to notice an
    unparsed heading itself, these tests will fail — update them (and the
    rationale in `check_board.py`) rather than removing the checks.
    """

    def setUp(self):
        self.board = board(
            "### 2026-10-04 01:00 UTC · Alice · note\n\nAlice's entry.\n",
            "### 2026-10-04 02:00 utc · Bob · note\n\nBob's whole entry.\n",
            "### 2026-10-04 03:00 UTC · Carol · note\n\nCarol's entry.\n",
        )

    def test_build_site_publishes_bobs_entry_under_alices_name(self):
        try:
            import build_site
        except ImportError:  # pragma: no cover
            self.skipTest("build_site.py not importable")
        _, entries = build_site.parse_board(self.board)
        self.assertEqual([e.author for e in entries], ["Alice", "Carol"])
        alice = entries[0]
        # Bob's heading and his words are now inside Alice's entry.
        self.assertIn("Bob", alice.body_html)
        self.assertIn("Bob's whole entry.", alice.body_html)

    def test_board_digest_counts_two_entries_where_three_were_written(self):
        try:
            import board_digest
        except ImportError:  # pragma: no cover
            self.skipTest("board_digest.py not importable")
        # ORIGINAL (before PR #38): rows = list(board_digest.entries(self.board))
        # #38 replaced entries() with parse(), which still leaves Bob out of the
        # count but now returns the heading it skipped instead of saying nothing.
        rows, skipped = board_digest.parse(self.board)
        self.assertEqual(len(rows), 2)
        self.assertNotIn("Bob", [raw for _, _, raw, _ in rows])
        self.assertEqual(len(skipped), 1)
        self.assertIn("Bob", skipped[0][1])

    def test_check_board_is_the_one_that_says_so(self):
        found = cb.scan_board(self.board)
        self.assertEqual([f.kind for f in found], ["unparsed heading"])

    def test_the_two_tools_disagree_about_one_heading_form(self):
        try:
            import board_digest
            import build_site
        except ImportError:  # pragma: no cover
            self.skipTest("tools not importable")
        heading = "### 2026-10-04 02:00 UTC\u00b7Bob\u00b7note"
        self.assertIsNotNone(build_site._ENTRY_HEADING.match(heading))
        # ORIGINAL (before PR #38): self.assertIsNone(board_digest.HEADING.match(heading))
        # #38 gave the digest the site's pattern, so the disagreement this test
        # recorded is closed; the pre-#38 pattern is kept in check_board.py and
        # still rejects the form, which is what the check above was written for.
        self.assertIsNotNone(board_digest.HEADING.match(heading))
        self.assertIsNone(cb._DIGEST_HEADING_BEFORE_38.match(heading))


class StampedInTheFuture(unittest.TestCase):
    """Added after this file's own author pushed an entry stamped 18m ahead."""

    def future_board(self, minutes_ahead: int) -> str:
        from datetime import datetime, timedelta, timezone

        when = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=minutes_ahead)
        return board(f"### {when:%Y-%m-%d %H:%M} UTC \u00b7 A \u00b7 note\n\nx\n")

    def test_an_entry_ahead_of_the_clock_is_noted(self):
        self.assertEqual(kinds(self.future_board(90)), ["stamped in the future"])

    def test_it_is_a_note_not_a_problem(self):
        self.assertEqual(severities(self.future_board(90)), [cb.NOTE])

    def test_an_entry_in_the_past_is_not_noted(self):
        self.assertEqual(kinds(self.future_board(-90)), [])

    def test_the_note_says_how_far_ahead_and_names_the_cause(self):
        detail = cb.scan_board(self.future_board(90))[0].detail
        self.assertIn("ahead of the clock", detail)
        self.assertIn("date -u", detail)

    def test_a_future_stamp_and_a_disorder_are_reported_separately(self):
        from datetime import datetime, timedelta, timezone

        ahead = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=120)
        text = board(
            f"### {ahead:%Y-%m-%d %H:%M} UTC \u00b7 A \u00b7 note\n\nx\n",
            "### 2026-01-01 01:00 UTC \u00b7 B \u00b7 note\n\ny\n",
        )
        self.assertEqual(
            sorted(kinds(text)), ["out of timestamp order", "stamped in the future"]
        )

    def test_the_check_decays_and_that_is_documented(self):
        # It compares against the clock when it runs, so the note vanishes once
        # the clock passes the stamp. A report that deletes itself deserves saying
        # so in the file rather than only in a commit message.
        src = Path(cb.__file__).read_text(encoding="utf-8")
        self.assertIn("decays", src)


if __name__ == "__main__":
    unittest.main()
