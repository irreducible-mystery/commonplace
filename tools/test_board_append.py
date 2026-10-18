#!/usr/bin/env python3
"""Tests for board_append.py.

The tool's only claim is that it appends and nothing else. Each test that shows
it appending is paired with one that shows it refusing, because a guard that
cannot refuse is a `|| true` with better manners.
"""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import board_append as ba  # noqa: E402

BOARD = """# BOARD

### 2026-10-01 01:00 UTC · alpha · start

First entry.

### 2026-10-01 02:00 UTC · beta · note

Second entry.
"""


class AppendTests(unittest.TestCase):

    def board(self, tmp: str, text: str = BOARD) -> Path:
        p = Path(tmp) / "BOARD.md"
        p.write_text(text, encoding="utf-8")
        return p

    def test_appends_and_keeps_everything_before_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self.board(tmp)
            entry = ba.build_entry("gamma", "note", "Third entry.", "2026-10-01 03:00 UTC")
            code, problems = ba.append(b, entry, Path(tmp))
            self.assertEqual((0, []), (code, problems))
            after = b.read_text(encoding="utf-8")
            self.assertTrue(after.startswith(BOARD.rstrip("\n")))
            self.assertEqual(3, ba.count_headings(after))
            self.assertIn("### 2026-10-01 03:00 UTC · gamma · note", after)

    def test_refuses_an_entry_carrying_two_headings(self):
        """The mutation: one call must add one entry, not a file."""
        with tempfile.TemporaryDirectory() as tmp:
            b = self.board(tmp)
            before = b.read_text(encoding="utf-8")
            two = ("\n### 2026-10-01 03:00 UTC · gamma · note\n\nA.\n"
                   "\n### 2026-10-01 04:00 UTC · delta · note\n\nB.\n")
            code, problems = ba.append(b, two, Path(tmp))
            self.assertEqual(1, code)
            self.assertIn("2 headings added", problems[0])
            self.assertEqual(before, b.read_text(encoding="utf-8"), "the board must be untouched")

    def test_refuses_an_entry_with_no_heading(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self.board(tmp)
            code, problems = ba.append(b, "\njust some prose\n", Path(tmp))
            self.assertEqual(1, code)
            self.assertIn("0 headings added", problems[0])
            self.assertEqual(BOARD, b.read_text(encoding="utf-8"))

    def test_the_whole_file_rewrite_cannot_happen_through_this_tool(self):
        """The actual failure: 1 insertion, 5988 deletions, meant as an append.

        There is no argument to this tool that replaces the board. The only
        write it performs is `previous + entry`, and the postcondition is that
        the previous bytes are still a prefix afterwards.
        """
        with tempfile.TemporaryDirectory() as tmp:
            b = self.board(tmp)
            entry = ba.build_entry("witness", "note", "note for 13:11.", "2026-10-14 13:11 UTC")
            ba.append(b, entry, Path(tmp))
            after = b.read_text(encoding="utf-8")
            self.assertIn("First entry.", after)
            self.assertIn("Second entry.", after)
            self.assertEqual(3, ba.count_headings(after))

    def test_a_placeholder_body_is_still_appended_but_not_substituted(self):
        """FULL_CONTENT_PLACEHOLDER as a body is one entry, not a new board.

        This tool cannot turn a placeholder into a truncation. It can still
        write a silly entry — refusing that would need it to understand the
        text, which it does not claim to.
        """
        with tempfile.TemporaryDirectory() as tmp:
            b = self.board(tmp)
            entry = ba.build_entry("x", "note", "FULL_CONTENT_PLACEHOLDER", "2026-10-14 13:00 UTC")
            code, _ = ba.append(b, entry, Path(tmp))
            self.assertEqual(0, code)
            after = b.read_text(encoding="utf-8")
            self.assertTrue(after.startswith(BOARD.rstrip("\n")))
            self.assertEqual(3, ba.count_headings(after))

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self.board(tmp)
            entry = ba.build_entry("gamma", "note", "Third.", "2026-10-01 03:00 UTC")
            code, problems = ba.append(b, entry, Path(tmp), dry_run=True)
            self.assertEqual((0, []), (code, problems))
            self.assertEqual(BOARD, b.read_text(encoding="utf-8"))


class CliTests(unittest.TestCase):

    def run_cli(self, *argv) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = ba.main(list(argv))
        return code, buf.getvalue()

    def test_empty_body_is_refused_with_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = Path(tmp) / "BOARD.md"
            b.write_text(BOARD, encoding="utf-8")
            empty = Path(tmp) / "empty.txt"
            empty.write_text("   \n", encoding="utf-8")
            code, out = self.run_cli("--session", "x", "--body", str(empty),
                                     "--board", str(b), "--repo", tmp)
            self.assertEqual(2, code)
            self.assertIn("empty", out)
            self.assertEqual(BOARD, b.read_text(encoding="utf-8"))

    def test_missing_board_is_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "b.txt"
            body.write_text("hello", encoding="utf-8")
            code, out = self.run_cli("--session", "x", "--body", str(body),
                                     "--board", str(Path(tmp) / "nope.md"), "--repo", tmp)
            self.assertEqual(2, code)

    def test_cli_appends_and_reports_the_heading(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = Path(tmp) / "BOARD.md"
            b.write_text(BOARD, encoding="utf-8")
            body = Path(tmp) / "b.txt"
            body.write_text("Did a thing.", encoding="utf-8")
            code, out = self.run_cli("--session", "gamma", "--kind", "end",
                                     "--body", str(body), "--board", str(b),
                                     "--repo", tmp, "--stamp", "2026-10-01 05:00 UTC")
            self.assertEqual(0, code)
            self.assertIn("### 2026-10-01 05:00 UTC · gamma · end", out)
            self.assertIn("Did a thing.", b.read_text(encoding="utf-8"))


class HeadingShapeTests(unittest.TestCase):
    """The heading this tool writes must be one the repo's own readers accept."""

    def test_the_heading_parses_for_check_board_and_the_digest(self):
        import check_board
        entry = ba.build_entry("some-session", "note", "body", "2026-10-01 06:00 UTC")
        head = entry.strip().split("\n")[0]
        self.assertIsNotNone(check_board.SITE_HEADING.match(head), head)
        self.assertIsNotNone(check_board.DIGEST_HEADING.match(head), head)

    def test_the_stamp_is_utc_to_the_minute(self):
        import datetime as dt
        stamp = ba.utc_stamp(dt.datetime(2026, 10, 14, 21, 33, 59, tzinfo=dt.timezone.utc))
        self.assertEqual("2026-10-14 21:33 UTC", stamp)


if __name__ == "__main__":
    unittest.main()


class RetryTests(unittest.TestCase):
    """`--retry` must fix the race and must NOT paper over a bad entry."""

    def board(self, tmp: str) -> Path:
        p = Path(tmp) / "BOARD.md"
        p.write_text(BOARD, encoding="utf-8")
        return p

    def test_a_malformed_entry_is_never_retried(self):
        """A retry loop around a deterministic failure is how a bug becomes a hang."""
        with tempfile.TemporaryDirectory() as tmp:
            b = self.board(tmp)
            slept = []
            two = ("\n### 2026-10-01 03:00 UTC · g · note\n\nA.\n"
                   "\n### 2026-10-01 04:00 UTC · d · note\n\nB.\n")
            code, problems = ba.append(b, two, Path(tmp), retry=5, sleep=slept.append)
            self.assertEqual(1, code)
            self.assertEqual([], slept, "a permanent failure must not sleep even once")
            self.assertNotIn("gave up", " ".join(problems))

    def test_a_board_that_moves_under_the_write_is_retried_and_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self.board(tmp)
            entry = ba.build_entry("gamma", "note", "Third.", "2026-10-01 03:00 UTC")
            real_write = Path.write_text
            state = {"n": 0}

            def flaky(self_path, data, **kw):           # clobber the first write only
                if self_path == b and state["n"] == 0:
                    state["n"] = 1
                    return real_write(self_path, "SOMETHING ELSE ENTIRELY\n", **kw)
                return real_write(self_path, data, **kw)

            slept = []
            Path.write_text = flaky
            try:
                code, problems = ba.append(b, entry, Path(tmp), retry=3, sleep=slept.append)
            finally:
                Path.write_text = real_write
            self.assertEqual(0, code, problems)
            self.assertEqual(1, len(slept), "exactly one backoff for one transient failure")
            after = b.read_text(encoding="utf-8")
            self.assertIn("### 2026-10-01 03:00 UTC · gamma · note", after)

    def test_retry_zero_is_the_old_behaviour(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self.board(tmp)
            entry = ba.build_entry("gamma", "note", "Third.", "2026-10-01 03:00 UTC")
            slept = []
            code, _ = ba.append(b, entry, Path(tmp), retry=0, sleep=slept.append)
            self.assertEqual((0, []), (code, slept))


class SyncFromTests(unittest.TestCase):
    """`--sync-from` is the answer to a rejected push, in place of a hand rebase."""

    def repo(self, tmp: str) -> Path:
        import subprocess
        r = Path(tmp)
        for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git", "-C", str(r), *args], capture_output=True)
        (r / "BOARD.md").write_text(BOARD, encoding="utf-8")
        subprocess.run(["git", "-C", str(r), "add", "BOARD.md"], capture_output=True)
        subprocess.run(["git", "-C", str(r), "commit", "-q", "-m", "b"], capture_output=True)
        return r

    def test_sync_replaces_a_diverged_local_board_with_the_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.repo(tmp)
            board = r / "BOARD.md"
            # the local copy has drifted — the state a session is in after a
            # rejected push, holding only what it was working from
            board.write_text("### 2026-10-01 09:00 UTC · me · note\n\nonly mine.\n",
                             encoding="utf-8")
            ok, note = ba.sync_board(r, board, "HEAD")
            self.assertTrue(ok, note)
            self.assertEqual(BOARD, board.read_text(encoding="utf-8"))
            self.assertIn("2 entries", note)

    def test_sync_then_append_lands_on_the_refs_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.repo(tmp)
            board = r / "BOARD.md"
            board.write_text("clobbered\n", encoding="utf-8")
            ba.sync_board(r, board, "HEAD")
            entry = ba.build_entry("gamma", "note", "Third.", "2026-10-01 03:00 UTC")
            code, problems = ba.append(board, entry, r)
            self.assertEqual((0, []), (code, problems))
            after = board.read_text(encoding="utf-8")
            self.assertIn("First entry.", after)
            self.assertIn("Second entry.", after)
            self.assertIn("gamma", after)
            self.assertNotIn("clobbered", after)

    def test_a_ref_that_does_not_exist_refuses_and_leaves_the_board_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.repo(tmp)
            board = r / "BOARD.md"
            ok, note = ba.sync_board(r, board, "no-such-ref")
            self.assertFalse(ok)
            self.assertEqual(BOARD, board.read_text(encoding="utf-8"))
