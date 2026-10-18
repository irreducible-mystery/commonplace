#!/usr/bin/env python3
"""Tests for assemble_board.py.

The migration's whole claim is that nothing moved. These check that, and check
the two ways a fragment scheme silently rewrites a record: by sorting on the
timestamp instead of the append position, and by treating two entries that
share a heading as one.
"""

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assemble_board as ab  # noqa: E402


PREAMBLE = "# BOARD\n\nA note before the entries.\n\n---"

E1 = "### 2026-10-01 01:00 UTC · alpha · start\n\nFirst."
E2 = "### 2026-10-01 02:00 UTC · beta · note\n\nSecond."
E3 = "### 2026-10-01 03:00 UTC · gamma · end\n\nThird."
OUT_OF_ORDER = "### 2026-09-30 23:00 UTC · delta · note\n\nStamped before the others, written after."


def board(*entries: str, preamble: str = PREAMBLE) -> str:
    # Mirrors assemble()'s join rule: a preamble is followed by exactly one
    # newline, and a board without one starts at its first heading.
    head = (preamble + "\n") if preamble else ""
    return head + "".join(e + "\n\n" for e in entries).rstrip() + "\n"


class RoundTripTests(unittest.TestCase):

    def round_trip(self, text: str):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "BOARD.md").write_text(text, encoding="utf-8")
            names = ab.split(root, text)
            return ab.assemble(root), names

    def test_a_board_reassembles_byte_identically(self):
        text = board(E1, E2, E3)
        rebuilt, names = self.round_trip(text)
        self.assertEqual(text, rebuilt)
        self.assertEqual(3, len(names))

    def test_a_board_with_no_preamble_reassembles(self):
        text = board(E1, E2, preamble="")
        rebuilt, _ = self.round_trip(text)
        self.assertEqual(text, rebuilt)

    def test_verify_reports_success_without_writing_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text = board(E1, E2)
            (root / "BOARD.md").write_text(text, encoding="utf-8")
            ok, note = ab.verify(root, text)
            self.assertTrue(ok, note)
            self.assertFalse((root / ab.ENTRIES).exists(), "verify must not touch the tree")


class OrderTests(unittest.TestCase):
    """Append order, not timestamp order. This is the whole design risk."""

    def test_an_out_of_order_entry_keeps_its_position(self):
        """26 entries in the real board are out of timestamp order and stay that way.

        Sorting fragments by their stamp would quietly repair all of them and
        print a cleaner board, which is the mistake the restore in #208 made
        on its first attempt — 0 order notes instead of 26.
        """
        text = board(E1, E2, OUT_OF_ORDER, E3)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "BOARD.md").write_text(text, encoding="utf-8")
            ab.split(root, text)
            rebuilt = ab.assemble(root)
        self.assertEqual(text, rebuilt)
        self.assertLess(rebuilt.index("delta"), rebuilt.index("gamma"),
                        "the late-written, early-stamped entry must stay where it was written")

    def test_the_sequence_and_not_the_filename_decides_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / ab.ENTRIES
            d.mkdir(parents=True)
            (root / ab.PREAMBLE).write_text("", encoding="utf-8")
            (d / "0002-aaa.md").write_text(E2 + "\n", encoding="utf-8")
            (d / "0001-zzz.md").write_text(E1 + "\n", encoding="utf-8")
            out = ab.assemble(root)
        self.assertLess(out.index("First."), out.index("Second."))

    def test_two_fragments_sharing_a_sequence_are_both_kept(self):
        """Keep both, for free: two sessions grabbing 0003 make two files."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / ab.ENTRIES
            d.mkdir(parents=True)
            (root / ab.PREAMBLE).write_text("", encoding="utf-8")
            (d / "0001-a.md").write_text(E1 + "\n", encoding="utf-8")
            (d / "0002-b.md").write_text(E2 + "\n", encoding="utf-8")
            (d / "0002-c.md").write_text(E3 + "\n", encoding="utf-8")
            out = ab.assemble(root)
        for marker in ("First.", "Second.", "Third."):
            self.assertIn(marker, out)
        self.assertEqual(3, out.count("### "))

    def test_duplicate_headings_survive_the_split(self):
        """Four headings in the real board carry two entries each."""
        twin = "### 2026-10-01 01:00 UTC · alpha · start\n\nA different alpha."
        text = board(E1, twin, E2)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = ab.split(root, text)
            rebuilt = ab.assemble(root)
        self.assertEqual(text, rebuilt)
        self.assertEqual(3, len(names), "a dedupe here would lose one of the pair")
        self.assertEqual(len(set(names)), len(names), "and the filenames must not collide")


class ProbeFailureTests(unittest.TestCase):
    """Empty and unreadable must not print the same thing as up-to-date."""

    def test_no_entries_directory_is_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "BOARD.md").write_text(board(E1), encoding="utf-8")
            with self.assertRaises(ab.ProbeFailure):
                ab.assemble(root)
            self.assertEqual(2, ab.main(["--root", str(root), "--check"]))

    def test_an_empty_entries_directory_is_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ab.ENTRIES).mkdir(parents=True)
            (root / "BOARD.md").write_text(board(E1), encoding="utf-8")
            self.assertEqual(2, ab.main(["--root", str(root), "--check"]))

    def test_a_fragment_with_no_sequence_prefix_is_exit_2(self):
        """A file with no place in the order must stop the build, not be skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / ab.ENTRIES
            d.mkdir(parents=True)
            (d / "0001-a.md").write_text(E1 + "\n", encoding="utf-8")
            (d / "notes.md").write_text(E2 + "\n", encoding="utf-8")
            (root / "BOARD.md").write_text(board(E1), encoding="utf-8")
            with self.assertRaises(ab.ProbeFailure):
                ab.assemble(root)
            self.assertEqual(2, ab.main(["--root", str(root), "--check"]))

    def test_splitting_a_board_with_no_entries_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ab.ProbeFailure):
                ab.split(Path(tmp), "# BOARD\n\nnothing yet.\n")


class CheckTests(unittest.TestCase):

    def test_check_is_zero_when_current_and_one_when_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text = board(E1, E2)
            (root / "BOARD.md").write_text(text, encoding="utf-8")
            ab.split(root, text)
            self.assertEqual(0, ab.main(["--root", str(root), "--check"]))
            (root / "BOARD.md").write_text(text.replace("Second.", "Edited."), encoding="utf-8")
            self.assertEqual(1, ab.main(["--root", str(root), "--check"]))
            self.assertEqual(0, ab.main(["--root", str(root), "--write"]))
            self.assertEqual(0, ab.main(["--root", str(root), "--check"]))
            self.assertIn("Second.", (root / "BOARD.md").read_text(encoding="utf-8"))

    def test_a_truncated_board_is_caught_by_check(self):
        """The failure this whole thing exists for, now a one-command detection."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text = board(E1, E2, E3)
            (root / "BOARD.md").write_text(text, encoding="utf-8")
            ab.split(root, text)
            (root / "BOARD.md").write_text("FULL_CONTENT_PLACEHOLDER\n", encoding="utf-8")
            self.assertEqual(1, ab.main(["--root", str(root), "--check"]))
            ab.main(["--root", str(root), "--write"])
            self.assertEqual(text, (root / "BOARD.md").read_text(encoding="utf-8"))


class RealBoardTests(unittest.TestCase):

    def test_this_repositorys_board_matches_its_fragments(self):
        root = Path(__file__).resolve().parent.parent
        if not (root / ab.ENTRIES).is_dir():
            self.skipTest("fragments not present in this checkout")
        self.assertEqual((root / "BOARD.md").read_text(encoding="utf-8"), ab.assemble(root))


if __name__ == "__main__":
    unittest.main()
