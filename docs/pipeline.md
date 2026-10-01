# Resolution, transcription and caching

[Back to the README](../README.md)

## How resolution and transcription work

The resolver parses the Apple show ID in the path and the separate episode ID in `i=`. It queries Apple's public show lookup with `entity=podcastEpisode&limit=200`, verifies both episode and collection IDs, fetches `feedUrl`, and matches the exact RSS GUID. Exact enclosure URL matching is the fallback. If the episode is missing, direct episode lookup and then the US storefront are attempted. It never guesses by title or assumes the Apple ID equals the RSS GUID.

The example resolves to **大鹏说中文 - Speak Chinese with Da Peng**, GUID `Buzzsprout-19793450`, reported duration **889 seconds (14:49)**, MIME `audio/mpeg`. See `resolver-example.json`.

```sh
python -m podcast_bot resolve "https://podcasts.apple.com/nl/podcast/id1490732024?i=1000789324203"
```

This CLI fetches only public metadata, with no credentials, audio download, or paid call.

Audio downloads stream in 64 KiB blocks, checking status, MIME, size, empty/truncated bodies, and duration. ffmpeg fully decodes before charging. Inputs must be below a conservative 24 MB upload ceiling; larger/longer audio is split near silence. Suitable MP3s are stream-copied; conversion uses mono 24 kHz, 64 kbps MP3 only when needed. Chunks are sequential, text is joined without removing deliberate repetition, and SRT times are offset.

Temporary directories delete originals/chunks on success, failure, or cancellation. On the next startup, abandoned job directories from an uncatchable kill are removed under the single-process lock. No original audio is retained. Cleanup happens as soon as ASR completes, before the text-only study stage.

## Caching, persistence, and billing

SQLite holds the queue, preferences, canonical-cache pointers, independent study-cache pointers, both stages' usage, and validated step checkpoints.

The ASR cache key uses stable Apple show/episode IDs, speech model, requested language, hints, and pipeline version. Study keys use the canonical transcript hash, text model, target/native languages, learner level, item targets, rendering settings, chunk budget, and prompt version. Changing HSK3 to HSK4 or ru to en does not invalidate ASR. Forced study generations use a new run namespace and replace the latest study-cache pointer only after success.

Completed files are published atomically with a hash manifest. A missing/corrupted derived file is rebuilt from validated checkpoints without another text-model call when possible. Study packs have their own immutable transcript copy; the original canonical file is never rewritten. Old packs remain available on disk after regeneration.

On restart, running jobs return to the queue. Completed ASR chunks and study requests are reused. An in-flight request with an unknown outcome requires explicit `/retry`, because it may already have been billed. Automatic SDK retries are disabled. Exactly-once billing cannot be guaranteed after a lost response or a crash. Failed delivery retries reuse the finished files.

Usage is separated in SQLite:

- `usage`: ASR duration, model, timestamp, outcome, optional token counts and estimated cost.
- `study_usage`: stage step, model, input characters/tokens, cached input tokens, output tokens, timestamp, outcome, estimated cost when known.

Prices checked on 2026-09-13: `gpt-transcribe` is listed at $0.0045/audio minute; 15/30/60 minutes cost approximately $0.0675/$0.135/$0.27 for one successful pass. The selected configurable study model, `gpt-5.4-mini`, is listed at $0.75/million input tokens, $0.075/million cached input tokens, and $4.50/million output tokens. Study costs depend on transcript and output length; no fixed episode price is promised. Estimates exclude taxes, regional uplift, hosting, and additional failed/repeated requests.
