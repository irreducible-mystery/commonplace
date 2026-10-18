#!/usr/bin/env python3
"""Tests for check_board_history.py.

Each test builds a throwaway git repository, commits a board, changes it, and
asks the checker what it sees. No test touches this repository's own history.

The design rule is the one `paradoxes/lean/probe_run.sh` states in the sibling
repo and this board's own `check_board.py` acts on: an instrument that cannot
fail certifies nothing. So every protective case is paired with the mutation it
is supposed to catch, and the probe-failure cases exist because a checker that
returns "clean" when it could not read the history prints the same thing as a
checker looking at a perfect record.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_board_history as cbh  # noqa: E402


ENTRY_A = """### 2026-10-01 01:00 UTC · alpha · start

First entry.
"""

ENTRY_B = """### 2026-10-01 02:00 UTC · beta · note

Second entry.
"""

ENTRY_C = """### 2026-10-01 03:00 UTC · gamma · end

Third entry.
"""


def board(*entries: str) -> str:
    return "# BOARD\n\n" + "\n".join(entries)


class Repo:
    """A throwaway git repository with a BOARD.md and a commit history."""

    def __init__(self, tmp: str):
        self.path = Path(tmp)
        self.path.mkdir(parents=True, exist_ok=True)
        self.board = self.path / "BOARD.md"
        self._git("init", "-q")
        self._git("config", "user.email", "t@t")
        self._git("config", "user.name", "t")

    def _git(self, *args):
        return subprocess.run(["git", "-C", str(self.path), *args],
                              capture_output=True, text=True, check=False)

    def commit(self, text: str, message: str = "board"):
        self.board.write_text(text, encoding="utf-8")
        self._git("add", "BOARD.md")
        self._git("commit", "-q", "-m", message)

    def audit(self, text: str | None = None):
        if text is not None:
            self.board.write_text(text, encoding="utf-8")
        return cbh.audit(self.path, "BOARD.md", self.board.read_text(encoding="utf-8"))

    def run(self, *argv) -> int:
        return cbh.main([str(self.board), "--repo", str(self.path), *argv])


class HistoryTests(unittest.TestCase):

    def test_unchanged_board_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.commit(board(ENTRY_A, ENTRY_B))
            missing, dropped, edited = r.audit()
            self.assertEqual(([], [], []), (missing, dropped, edited))
            self.assertEqual(0, r.run("--quiet"))

    def test_a_removed_entry_is_caught(self):
        """The mutation. Without it the test above certifies nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.commit(board(ENTRY_A, ENTRY_B))
            r.commit(board(ENTRY_A), "drop beta")
            missing, dropped, edited = r.audit()
            self.assertEqual(1, len(missing))
            self.assertIn("beta", missing[0][0])
            self.assertEqual([], dropped)
            self.assertEqual(1, r.run("--quiet"))

    def test_an_emptied_board_reports_every_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.commit(board(ENTRY_A, ENTRY_B, ENTRY_C))
            r.commit("FULL_CONTENT_PLACEHOLDER\n", "restore full content")
            missing, _, _ = r.audit()
            self.assertEqual(3, len(missing))
            self.assertEqual(1, r.run("--quiet"))

    def test_an_appended_correction_is_not_a_loss(self):
        """Entries grow. A checker that reddens on growth would be unusable."""
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.commit(board(ENTRY_A))
            grown = ENTRY_A.rstrip() + "\n\nCorrection appended later.\n"
            r.commit(board(grown), "append a correction")
            missing, dropped, edited = r.audit()
            self.assertEqual(([], [], []), (missing, dropped, edited))
            self.assertEqual(0, r.run("--quiet"))

    def test_a_rewritten_body_is_a_note_not_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.commit(board(ENTRY_A))
            rewritten = "### 2026-10-01 01:00 UTC · alpha · start\n\nSomething else entirely.\n"
            r.commit(board(rewritten), "rewrite alpha")
            missing, dropped, edited = r.audit()
            self.assertEqual([], missing)
            self.assertEqual([], dropped)
            self.assertEqual(1, len(edited))
            self.assertEqual(0, r.run("--quiet"))     # a note never fails

    def test_keep_both_a_dropped_duplicate_is_caught(self):
        """Two entries under one heading, both kept. Losing one must not pass.

        This is the case a heading-keyed checker gets wrong: the heading is
        still there, so nothing looks missing. Four headings in the real board
        are like this.
        """
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            twin = "### 2026-10-01 01:00 UTC · alpha · start\n\nA different alpha.\n"
            r.commit(board(ENTRY_A, twin, ENTRY_B))
            r.commit(board(ENTRY_A, ENTRY_B), "quietly lose one of the pair")
            missing, dropped, edited = r.audit()
            self.assertEqual([], missing, "the heading is still present, so this is not 'missing'")
            self.assertEqual(1, len(dropped))
            head, was, now, _ = dropped[0]
            self.assertIn("alpha", head)
            self.assertEqual((2, 1), (was, now))
            self.assertEqual(1, r.run("--quiet"))

    def test_text_before_the_first_heading_is_not_an_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.commit(board(ENTRY_A))
            with_note = "# BOARD\n\nA restore note nobody wrote as an entry.\n\n" + ENTRY_A
            missing, dropped, edited = r.audit(with_note)
            self.assertEqual(([], [], []), (missing, dropped, edited))


class ProbeFailureTests(unittest.TestCase):
    """A checker that cannot read the history must not print a clean board."""

    def test_no_history_for_the_path_is_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.commit(board(ENTRY_A))
            with self.assertRaises(cbh.ProbeFailure):
                cbh.history(r.path, "NOT_THE_BOARD.md")
            self.assertEqual(2, r.run("--path", "NOT_THE_BOARD.md", "--quiet"))

    def test_not_a_git_repository_is_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "BOARD.md"
            plain.write_text(board(ENTRY_A), encoding="utf-8")
            self.assertEqual(2, cbh.main([str(plain), "--repo", tmp, "--quiet"]))

    def test_unreadable_board_is_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.commit(board(ENTRY_A))
            self.assertEqual(2, cbh.main([str(Path(tmp) / "nope.md"),
                                          "--repo", tmp, "--path", "BOARD.md", "--quiet"]))

    def test_a_shallow_clone_is_exit_2_not_a_clean_board(self):
        """The quiet way this check becomes a `|| true`.

        `actions/checkout` fetches depth 1 by default. One commit in reach means
        no past to be missing from, so the walk finds exactly what it was handed
        and every board looks whole — including one with 377 entries gone.
        """
        with tempfile.TemporaryDirectory() as tmp:
            origin = Repo(str(Path(tmp) / "origin"))
            origin.commit(board(ENTRY_A, ENTRY_B))
            origin.commit(board(ENTRY_A), "drop beta")
            shallow = Path(tmp) / "shallow"
            subprocess.run(["git", "clone", "-q", "--depth", "1",
                            "file://" + str(origin.path), str(shallow)],
                           capture_output=True, text=True, check=False)
            if not (shallow / ".git").exists():
                self.skipTest("git clone --depth is unavailable here")
            with self.assertRaises(cbh.ProbeFailure):
                cbh.history(shallow, "BOARD.md")
            self.assertEqual(2, cbh.main([str(shallow / "BOARD.md"),
                                          "--repo", str(shallow), "--quiet"]))

    def test_a_history_that_parses_to_zero_entries_is_exit_2(self):
        """Zero entries everywhere means the parser broke, not that nobody wrote."""
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.commit("## 2026-10-01 01:00 UTC · alpha · start\n\nWrong heading level.\n")
            with self.assertRaises(cbh.ProbeFailure):
                cbh.history(r.path, "BOARD.md")
            self.assertEqual(2, r.run("--quiet"))


class RealBoardTests(unittest.TestCase):
    """The board in this repository, as committed, must hold its own history."""

    def test_this_repositorys_board_is_whole(self):
        repo = Path(__file__).resolve().parent.parent
        board_file = repo / "BOARD.md"
        if not (repo / ".git").exists() or not board_file.exists():
            self.skipTest("not a checkout of this repository")
        missing, dropped, _ = cbh.audit(repo, "BOARD.md",
                                        board_file.read_text(encoding="utf-8"))
        self.assertEqual([], missing, f"{len(missing)} entries have left the published board")
        self.assertEqual([], dropped, f"{len(dropped)} headings lost a duplicate")


if __name__ == "__main__":
    unittest.main()
