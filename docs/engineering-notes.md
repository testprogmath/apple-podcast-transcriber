# Engineering notes

Four problems found in this bot in October 2026, and how each was traced. They are written up because the cause was never where the first symptom pointed.

## The deployment that failed at its own backup

On 14 October the deployment of #42 stopped with `Deployment failed (OperationalError)`. The run of #39 before it had failed the same way, so production was three releases behind `main`, still serving #40.

The message is deliberately terse. The deployment helper reports only the exception type, because subprocess output and environment values can contain secrets, and that rule held up the diagnosis more than anything else. `OperationalError` meant SQLite, and the first suspect was a write lock held by the bot during the drain. A zero-timeout `BEGIN IMMEDIATE` against the production database came back free, with no running jobs. The disk did not: 75 GB used and 0 MB available. Before every release the helper copies the database into `backups/`, and SQLite cannot open a new file on a full disk.

Each release streams a 1.5 GB image to the server, and nothing had ever removed one. There were 38 release images and 16 older manual and rollback tags. Deleting the 52 that no container used freed 37 GB, and the re-run of #42 deployed normally.

The lasting fix (#43) makes the helper remove every release image except the new one and the one it replaced, which stays as the rollback target. Pruning runs only after the new release is healthy, and a pruning failure is reported without touching the live release. The first deployment after it logged `Removed 1 old release images`. The helper is installed outside the checkout, so merging did not update it; it had to be copied to the server by hand, a step easy to forget.

## Dependabot and a lock file it could not read

A grouped Dependabot update (#35) failed CI with `ResolutionImpossible`: pydantic 2.14.0 requires pydantic-core 2.50.0, and `requirements.txt` still pinned 2.46.5.

`requirements.txt` is a lock compiled by `uv pip compile`, transitive pins included. Dependabot's `pip` ecosystem treats it as a plain requirements file and edits only the `==` lines of direct dependencies. Looking for earlier damage of the same kind turned up a worse case. An older update (#29) had moved openai to 3.19.2, which requires `httpx2`, without pinning it. The production image had installed `httpx2`, `httpcore2` and `truststore` unpinned for weeks.

GitHub's documentation describes Dependabot's uv support only for `uv.lock`. The source of `dependabot-core` showed a second path: its uv compile updater recompiles `requirements.txt` from a `requirements.in`, and only from `.in` files. The fix (#38) moves the runtime ranges into `requirements.in`, has `pyproject.toml` read them as its dependencies, and switches Dependabot to the `uv` ecosystem.

One change nearly went out broken. The Docker image installs the package with `pip install --no-deps .`, and without `requirements.in` in the build context setuptools builds a package with no runtime dependencies and reports no error. A test now fails if the Dockerfile stops copying that file. Dependabot's next grouped update (#39) recompiled the lock correctly and moved pydantic-core with pydantic.

## A Reader that read 都 as a surname

The bug surfaced in a README screenshot: 都 was shown as Dū, 中 as Zhōng and 比 as Bǐ, with "surname Du" as the first meaning of 都.

The Reader took a word's pinyin and first meaning from its lowest CC-CEDICT entry. CC-CEDICT often lists proper nouns first, capitalised: the surname Du, China, Belgium. The obvious fix, taking the first entry with lowercase pinyin, was checked against the real dictionary before it went anywhere. It changed the reading of 1,572 of 196,389 headwords, and two of the frequent characters inspected came out wrong: 比 became bī and 华 huā, because CC-CEDICT also lists rare readings before common ones.

The fix that shipped (#48) prefers, among common-word entries, the one whose reading pypinyin also gives, and falls back to the first common-word entry. A word with only proper-noun entries, such as 中国, keeps its capitals, and the chosen entry's meanings come first. Across the frequent characters inspected by hand the changes went the expected way (说 shuō, 看 kàn, 着 zhe, 还 hái), with two debatable standalone readings left: 长 zhǎng and 地 dì. One older decision was reversed on purpose: standalone 行 now reads xíng rather than háng, and the test that pinned háng was rewritten.

## Original audio that vanished for a quarter of an episode

In one news episode, 25 of 103 sentences played system speech instead of the podcast's own audio. The episode had word-level timestamps from Whisper, 4,140 words whose text matched the transcript exactly, 4,478 characters on both sides.

Alignment is conservative by design: a sentence gets a range only when its start and end both fall on unit boundaries, and a unit is never split. It turned out the Reader was not using the words at all. `align()` rejected the whole word stream at the first word whose start equalled its end, and Whisper had produced 202 of those. The Reader fell back to segments of up to 110 characters, and sentences sharing a segment lost their audio.

The first candidate fix, accepting zero-length words, moved the episode from 78 sentences to 78. A second rule was rejecting the stream as well: 19 punctuation-only units such as `·` and `。`. With both relaxed (#54), and every other check kept, the episode reaches 103 of 103 at word granularity. Run read-only against every podcast document in production, sentences with original audio go from 162 to 190 of 455. The three documents still without audio cannot be recovered by alignment: two were transcribed by a model that returns no timestamps, and one predates stored timing.
