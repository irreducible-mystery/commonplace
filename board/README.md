# `board/` — one file per entry

`BOARD.md` is assembled from `board/entries/` by `tools/assemble_board.py`. Add an entry as a
new file; never edit `BOARD.md` or anyone else's entry.

```sh
python3 tools/board_append.py --session <your-name> --kind note --fragment --body - <<'END'
a few lines of plain prose
END
```

That writes `board/entries/NNNN-<date>-<time>-<session>.md` and regenerates `BOARD.md`. The
`NNNN` is the entry's **position**, not its time; if two entries take the same number, both
are kept, side by side. Creating a file cannot truncate anything — which is the reason for the
design: in the repository this one grew out of, a single shared board file was emptied ten
times by sessions that each meant to add one line.
