# Dutch dictionary distribution operations

The dictionary is distributed separately from the application image using public
GitHub Release assets. The repository is public; the existing manual application
release workflow is compatible, but dictionary releases use their own tag namespace
and do not bump the application version. No release has been published by this work.
No Docker build, application startup or lookup invokes these tools or downloads data.

## Contract and trust

Three files travel together: `dutch.sqlite3` (the existing artifact filename),
`dutch-artifact.json`, and `LICENSE-DUTCH.txt`. No archive or extraction is involved.
The generated manifest contains artifact/notice byte sizes and SHA-256, schema and
builder versions, all source hashes, URL, dates, extractor revisions, transformation,
language, license, attribution and semantic counts. SQLite metadata must match it.
The notice is generated from the actual artifact provenance, including source pins.

Content version: `enwiktionary-<dump YYYYMMDD>-<extraction YYYYMMDD>-rN`.
Release tag: `dutch-dictionary-<content>-s<schema>-b<builder>`.
Increase `rN` for changed content/packaging with the same dates; never overwrite a
published tag or assets. Schema/builder versions are compatibility contracts;
application `v*` versions are independent. Runtime supports schema/builder 1 and
compatible content updates from the same source/license contract. Unknown schema
or builder versions fail open in Reader and are rejected by the installer.

The manifest SHA-256 is the trust anchor. Obtain it from a reviewed trusted Git
revision or the publisher's independently trusted receipt, never by hashing a
manifest just downloaded from an untrusted release URL. The pin for the checked-in
release manifest is:

```text
72017851df196c11fe60fa8e638a646fddfae31ec9baad14ad785bf78e6163f9
```

An arbitrary HTTPS host is allowed for mirrors, but cannot change accepted bytes
without the pin changing. HTTPS and redirects must stay HTTPS. No authentication
or credentials are required. Network errors omit remote URLs and redirect tokens.
Only the three fixed asset names are read; sizes are bounded before hashing and
SQLite is opened read-only for schema, metadata, integrity, foreign-key, count and
nine representative lexical checks. Downloads are never executed.

## BUILD and VALIDATE (offline)

Run from the repository checkout with its Python environment installed. Output
belongs in ignored `dist/`, with source files outside Git. Fetching/archiving a new
raw snapshot is a separate explicit research operation requiring network; the
moving upstream URL is not a reproducible source pin. Retain pinned inputs in
controlled storage. The existing builder verifies the source hash before building.

```sh
python -m tools.dutch_dictionary_distribution package \
  --source /path/to/pinned/raw-dutch.jsonl --source-kind subset \
  --revision r1 --output dist/dutch-release
```

For the pinned full gzip, use `--source-kind compressed --source /path/to/raw.jsonl.gz`.
For a future reviewed source snapshot, supply `--snapshot-json /path/to/snapshot.json`
containing the complete updated `SNAPSHOT` mapping from `podcast_bot/reader/dutch.py`
(source identity, dates, both source hashes, revisions, license, attribution and
transformation). Do not merely change dates while reusing old source checksums.
The package tool derives metadata and counts from the built artifact; hashes and
sizes are automatic. New schema/builder versions need an explicit code review.

Prefer reusing the already validated 46 MB artifact when its source has not changed:

```sh
python -m tools.dutch_dictionary_distribution package \
  --artifact /path/to/validated/dutch.sqlite3 --revision r1 \
  --output dist/dutch-release
```

Both commands refuse to overwrite an existing output directory, validate the entire
bundle, and print the version plus manifest/artifact checksums. No lexical fallback
or exception is injected; the nine words are packaging checks only. Copy the generated
manifest and notice into `dictionary/` for review when preparing a new release.
Validate using the independently trusted receipt pin:

```sh
DICT_PIN=72017851df196c11fe60fa8e638a646fddfae31ec9baad14ad785bf78e6163f9
python -m tools.dutch_dictionary_distribution validate \
  --assets dist/dutch-release --manifest-sha256 "$DICT_PIN"
```

Reusing the real artifact preserves its exact SHA-256
`d698f7098b5b44a7e32dbc0f01959899fe0467c6082ebb60b568fd3d8ff7248b`
and 46,010,368 bytes. Byte-identical SQLite rebuilding across SQLite/toolchain
versions is not guaranteed. Reproducibility means pinned source, builder commit,
schema, extractor revisions and validated semantic counts; each actual build gets
its own verified byte checksum. Tiny fixture rebuilding is also tested for byte
identity on the current toolchain, without making a cross-toolchain promise.

## PUBLISH (future, network; do not run during ordinary deployment)

After reviewing/committing manifest, notice and source changes, use the package
receipt version. This command sequence intentionally fails if a release/tag already
exists; do not add `--clobber`, force a tag, or use a mutable `latest` pin.
GitHub's optional release immutability setting is not assumed; independent manifest
pins detect asset replacement even without that setting.

```sh
DICT_TAG=dutch-dictionary-enwiktionary-20260902-20261003-r1-s1-b1
# Run VALIDATE above first, from the reviewed builder commit.
git tag -a "$DICT_TAG" -m "Dutch dictionary $DICT_TAG"
git push origin "refs/tags/$DICT_TAG"
gh release create "$DICT_TAG" --repo testprogmath/apple-podcast-transcriber \
  --verify-tag --latest=false --title "$DICT_TAG" \
  --notes "Dutch dictionary data: CC BY-SA 4.0. See bundled LICENSE-DUTCH.txt and dutch-artifact.json for attribution, provenance and integrity." \
  dist/dutch-release/dutch.sqlite3 \
  dist/dutch-release/dutch-artifact.json \
  dist/dutch-release/LICENSE-DUTCH.txt
```

Publication requires publisher GitHub authorization. Reader and installation of
public assets do not. The three separate assets avoid archive traversal and an
extra archive checksum contract. Application release automation is unchanged.

## INSTALL and UPGRADE (explicit network, or offline local assets)

Run on the deployment host from a trusted checkout/environment as an operator
able to write the existing data mount. Use a dedicated root; keep its parent
traversable by container UID 1000. Never give runtime ownership of release credentials.
For the checked-in manifest, the exact future public release install is:

```sh
DICT_TAG=dutch-dictionary-enwiktionary-20260902-20261003-r1-s1-b1
DICT_PIN=72017851df196c11fe60fa8e638a646fddfae31ec9baad14ad785bf78e6163f9
python -m tools.dutch_dictionary_distribution install \
  --root data/dictionaries --manifest-sha256 "$DICT_PIN" \
  --release-url "https://github.com/testprogmath/apple-podcast-transcriber/releases/download/$DICT_TAG"
```

Until publication, or for an air-gapped host, replace `--release-url ...` with
`--source-dir /path/to/verified/release-assets`. Both paths perform the same checks.
Set `.env` to `READER_DUTCH_DICTIONARY=/app/data/dictionaries/current/dutch.sqlite3`.
The existing Compose `./data:/app/data` mount survives image/container replacement.
Restart the bot using the existing deployment procedure after installation.
Do not install using the image receiver's forced SSH key as a general-purpose shell.

Upgrade by running the same installer with a new explicit tag and independently
trusted manifest pin, verify, then restart. Repeating the exact installed pin fully
verifies local assets and returns `already_current` without contacting the source.
No startup network is required, including when the artifact is absent.

## Atomicity and failure behavior

Installers serialize through POSIX `flock` on `.install.lock` (Linux/macOS).
Download into `.download-*` under `versions/` on the same filesystem; fsync asset
files and the complete verified generation directory. Rename the directory to
`versions/<tag>.<manifest-sha256>/`, fsync `versions/`, then atomically replace a
relative `current` symlink and fsync its parent. Bundle selection switches SQLite,
manifest and notice together. Files are readable 0644, directories 0755, lock 0600;
these directories should be writable only by trusted operators.

Before pointer replacement, download/verification/interruption failures preserve
the previous selection. After the replacement commit point, a power loss or fsync
error can leave the old or new complete verified generation selected; neither is
partially copied. Local I/O errors instruct the operator to VERIFY before retrying.
Old generations remain for rollback. Normal failures clean temporary files; a
SIGKILL can leave staging debris, cleaned under lock by the next installer run.
Never remove the lock file while another installer could be active.

Reader resolves the path once and keeps a read-only SQLite handle; an existing
Reader continues using its old generation during a switch. Restart to use the new
one. Do not edit/delete generation files behind a running Reader. There is no
automatic garbage collection. An unmanaged/broken `current` pointer is rejected.

CLI JSON status/exit: `installed`, `already_current`, `verified`, `not_installed`
exit 0; `verification_failed` 2; `incompatible_schema` 3 (including builder mismatch);
`download_failed` 4; local `install_failed` 5. Network timeouts are explicit installer
failures, never a fallback to unverified bytes. Reader independently fails open for
missing, corrupt or incompatible databases; contextual lookup remains independent.

## VERIFY, ROLLBACK, REMOVE/DISABLE (offline)

```sh
python -m tools.dutch_dictionary_distribution verify \
  --root data/dictionaries --manifest-sha256 "$DICT_PIN"
python -m tools.dutch_dictionary_distribution rollback \
  --root data/dictionaries --version "$DICT_TAG" --manifest-sha256 "$DICT_PIN"
```

For rollback, use the old known-good tag and pin retained in your deployment record;
that complete generation must still exist locally. Verification precedes the same
atomic pointer switch. Restart the bot afterwards. `verify` without a pin uses the
pin recorded in the generation name: this checks local consistency and reports
version/hash/schema/builder, but does not independently authenticate local changes.

Disable by removing/commenting `READER_DUTCH_DICTIONARY` in `.env` and restarting
(the default image contains no Dutch database). Alternatively point it at a deliberately
absent file. Verify contextual lookup still works. To remove data, stop Reader and
all installers, then remove only the dedicated `data/dictionaries` directory after
retaining any wanted rollback bundles elsewhere. Ordinary restarts reuse installed
data; removing it is an explicit operator action, never part of deployment.

## Validation and limits

Tiny synthetic fixtures cover source packaging, manifest pins, schema/builder,
byte bounds, corrupt/truncated data even with matching download hashes, failed
network, interruption before replacement, retained generations, concurrent installers,
rollback, already-current/offline operation, and Reader holding its old handle.
Ordinary CI runs these tests without fetching raw data or release assets.

The existing real artifact was packaged without rebuilding, installed into a fresh
local data root through the offline asset source, verified, and read through Reader.
All nine lookups (`zolder`, `huis`, `huizen`, `kind`, `kinderen`, `mens`, `jongens`,
`museum`, `musea`) preserved lemma/form behavior; sense ambiguity/article evidence
continues to come exclusively from the existing resolver. Counts remain 146492
entries, 88140 senses, 88655 glosses, 200974 surfaces, 451347 forms, 102474 relations.

Acceptable v1 limits: POSIX filesystem/locking required; operators retain/manage
old generations and restart Reader; schema/builder compatibility is explicit rather
than a migration engine. Follow-up: publish the reviewed assets and perform an
operator-authorized production install. Real GitHub download/deployment validation
awaits that publication; this task exercises local real assets and mocked HTTPS.

Final validation on this branch: 23 new distribution tests; focused distribution,
existing Dutch dictionary and Reader language tests **104 passed**; full Python
suite **1111 passed**. Ruff lint and format checks, JavaScript syntax check for
`app.js`, and `git diff --check` passed. Frontend source was unchanged, so frontend
behavior suites were not rerun. No SQLite/raw data, deployment, publication or
production restart is part of this change.
