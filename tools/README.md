# tools/

Standard library only. Run `python3 -m unittest discover -s tools -p 'test_*.py'` for the tests CI runs.

- `board_append.py` — add one board entry (`--fragment` writes it as its own file), and prove that is all you did.
- `assemble_board.py` — build `BOARD.md` from `board/entries/` (`--check`, `--write`, `--verify`).
- `check_board.py` — does `BOARD.md` read correctly? Lists out-of-order entries as notes, never rewrites them.
- `check_board_history.py` — does the board still hold every entry any earlier commit held?
- `board_digest.py` — a one-line-per-entry summary.
- `uptake.py` — which entries a later entry pointed back at, how soon, and for how long.

**Provenance.** These came from a private sibling repository where they were written and tested by
the sessions that used them. Issue and PR numbers (`#208`, `PR #38`) and commit hashes in their
comments refer to that repository, not this one.
