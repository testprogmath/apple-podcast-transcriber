# CC-CEDICT dictionary data

Third-party data, licensed separately from this project. See
[LICENSE-CC-CEDICT.txt](LICENSE-CC-CEDICT.txt): **CC BY-SA 4.0**, published by MDBG.

## Pinned snapshot

| | |
| --- | --- |
| File | `cedict_ts.u8.gz` (upstream, unmodified) |
| Upstream | https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz |
| Snapshot date | `2026-09-14T05:30:59Z` (from the file's own `#! date=` header) |
| Format | `#! version=1 subversion=0 format=ts charset=UTF-8` |
| Entries | 125,061 declared, 125,061 parsed |
| Size | 3,974,289 bytes compressed, 9,847,992 uncompressed |
| SHA-256 | `5b8618e6db82567ba1bb07e4fa1e6edf060ef32262510700df910c50833d1aff` |

The snapshot is committed rather than downloaded during the image build, so builds
are hermetic and reproducible without depending on an upstream host being reachable.

## Building the runtime database

```sh
python -m podcast_bot.reader.cedict dictionary/cedict_ts.u8.gz podcast_bot/reader/cedict.sqlite3
```

Roughly 0.6 s, producing a 14.6 MB SQLite database. Output is byte-for-byte
deterministic for a given snapshot: rows are inserted in source order with
sequential ids, then `VACUUM`ed.

The Docker image runs this during build. The generated database is **not** committed;
`.gitignore` excludes it. Without it the Reader still works and simply shows no
dictionary definitions.

## Updating the snapshot

Download a fresh copy from the upstream URL above, replace `cedict_ts.u8.gz`, and
update the snapshot date, entry count, sizes and checksum in this table. Verify with:

```sh
shasum -a 256 dictionary/cedict_ts.u8.gz
python -m pytest -q tests/test_dictionary.py
```
