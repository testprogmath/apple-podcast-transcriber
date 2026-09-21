# Telegram commands

[Back to the README](../README.md)

The private chat has a Telegram command menu with Russian descriptions. Tap **Menu** or type `/`. Commands such as `/language` and `/transcribe` still require the argument shown in their description.

```text
<Apple Podcasts episode URL>
/transcribe de <URL>
/transcribe en <URL>
/transcribe nl <URL>
/transcribe auto <URL>
/level HSK3
/level HSK4
/language zh
/native ru
/regenerate
/regenerate HSK4
/zip
/srt
/reader
/mosaic
/hanly
/add_hanly 不知不觉
/status
/retry
/force <URL>
/help
/start
```

- A plain URL uses the configured source language and current study preferences.
- `/level`, `/language`, and `/native` persist preferences in SQLite across restarts. Levels can be HSK3, HSK4, A2, B1, etc.; they are not hardcoded to Mandarin.
- `/regenerate` makes a **new text-model run** from the most recent canonical transcript. It never calls speech recognition or downloads audio. The optional level applies to that run; `/level` changes the lasting preference.
- Re-sending a URL reuses the transcript and matching study pack. After a level/native-language change it creates only new study materials.
- `/force <URL>` intentionally makes a new paid audio transcription. `/retry` resumes a failed job, reusing completed stage checkpoints.
- `/zip` retrieves the latest completed pack, even if a newer job is still processing.
- `/srt` sends subtitles for the latest episode. Nothing is generated on demand: the file exists only when the transcription model timed the episode.
- The finished-job message links the episode audio and its Apple Podcasts page, so the recording sits beside its transcript.
- `/reader` opens the latest transcript or study pack in the Reader. It never retranscribes; it reuses the stored transcript.
- `/add_hanly <text>` files any Chinese word, phrase or sentence as a single Hanly card, with pinyin, a Russian meaning and a translated example in its note, and no episode, transcript or study pack involved.
- Long jobs update one status message. Study failures leave the transcript usable and deliver it on its own, with an explanation and `/retry` guidance.

## MP3 downloads (Apple Podcasts and YouTube)

- `/mp3 https://podcasts.apple.com/…?i=…` downloads an episode as an MP3.
- `/mp3 https://www.youtube.com/watch?v=…` downloads **audio only** from one public,
  finished video. Short `youtu.be` and Shorts links also work. A video link with
  a playlist parameter processes only that video; playlist/channel links and live
  streams are rejected.
- `/mp3` without a URL uses the latest saved podcast's Apple link.

The bot sends a Telegram audio player/file with title, author and duration. This
command does not transcribe, generate study materials, or call paid AI APIs.
Ordinary YouTube messages do not start transcription; use `/mp3` explicitly.

Only one MP3 request runs at a time, in a background task; other commands remain
responsive. Existing duration/download limits apply. MP3 uses 128 kbps where it
fits, falling back to 96/64/48/32 kbps for longer recordings, with a checked 49 MB
ceiling. Recordings too long to fit are refused, not silently cut. Temporary
source and output files are removed after delivery, failure or cancellation;
leftovers from a crash are removed on startup. Interrupted requests are not
persistently queued: send the command again. Requests download the source again;
existing transcript/Reader caches and temporary transcription audio are unchanged.

YouTube extraction uses pinned `yt-dlp` and matching `yt-dlp-ejs`; Docker includes
Node 22, and local installations need Node 22+ plus the existing ffmpeg/ffprobe.
The downloader selects HTTPS **audio-only** streams with no video fallback.
Downloaded media uses the existing size-limited HTTP streaming and public-address
checks on redirects. No browser cookies, account login, thumbnails or arbitrary
site extractors are used. Host blocks/private videos can still fail with a safe
message; keep yt-dlp and its matching EJS version updated together.

References: [yt-dlp runtime requirements](https://github.com/yt-dlp/yt-dlp/wiki/EJS)
and [Telegram sendAudio](https://core.telegram.org/bots/api#sendaudio).

## Existing YouTube subtitles

`/subs <YouTube URL>` sends the video's existing subtitles as **SRT + TXT**.
It uses the configured default language (`auto` defaults to `zh`); specify another
with `/subs en <URL>` or `/subs zh-Hant <URL>`. Published tracks are preferred;
existing YouTube automatic captions are a fallback and are labelled as such.
Auto-translations are not selected. Missing tracks produce a clear message;
there is no audio/video download, paid API call or new speech recognition.

This runs separately in the background, one subtitle request at a time. Caption
files are limited to 2 MB, validated as SRT, and removed after delivery or failure.
Text output removes timing/indexes and subtitle markup, preserving cue order and
repeated text. `/srt` retains its existing meaning: the latest podcast's subtitles.

## Study materials from an uploaded SRT

Send a UTF-8 `.srt` document to the bot. Use `/language zh` (or `en`, `de`, `nl`,
etc.) beforehand to identify its language; uploads do not guess language from
filenames or run speech recognition.

For Chinese, the canonical subtitle text opens in Reader immediately when Reader
is configured. With study generation enabled, a study-only job produces the
existing translation, vocabulary, Hanly CSV, grammar notes and Mosaic sentences,
plus `exercises.md`: gap-fill exercises and answers using exact source quotes.
Existing configured Hanly/Mosaic uploads apply only to Chinese. Other languages
receive the transcript and vocabulary/expressions, without external uploads;
the current Reader remains Chinese-only. Text generation is billed normally;
importing and parsing SRT itself makes no model requests.

The original SRT, cleaned text and cue timing survive restart. Markup/indexes and
timecodes are excluded from Reader text; repeated cues are preserved. Files must
be non-empty UTF-8, at most 2 MB and 200,000 text characters, with valid ordered
start/end times. Overlapping cues can be imported, but are not asserted to be
precisely playable sentence ranges. Identical uploads reuse jobs/materials;
different cue timing or languages have distinct source/cache identities.
`/regenerate` uses the saved subtitle source without audio transcription, `/srt`
returns the imported file, and the reply to the upload opens it in Reader.

An uploaded SRT alone has no audio association: Reader uses system TTS. Files are
never paired with audio by filename. `/mp3` downloads remain temporary.

## YouTube subtitles with original Reader audio

Send `/youtube zh <YouTube URL>` (or omit `zh` to use the configured language).
The bot imports existing captions, downloads audio only, saves one MP3, and starts
the same Reader/study workflow. There is no speech recognition; normal study text
generation costs still apply. `/subs` continues to download captions only.

Reader 🔊 prefers the saved recording when a sentence maps exactly to complete
subtitle cues. Ambiguous boundaries use system TTS; no estimated intra-cue timing
or separate sentence clips are generated. Audio download, duration validation or
storage failures preserve the subtitles and allow study generation with TTS.
Labelled audio tracks must match the requested language; when YouTube supplies no
track language labels, its default audio is used. Missing captions stop the request.

Audio lives in persistent `DATA_DIR/media/`, addressed by content hash and reused
across requests. `READER_AUDIO_STORAGE_MB=2000` limits retained MP3 storage; each
file is capped at 49 MB. Files are not automatically evicted. Restart removes only
unfinished temporary copies. Back up media together with the existing data volume.
Cached study packs can use newly attached audio without regenerating materials.

Authenticated Reader document access issues a document/owner-scoped, 12-hour
HttpOnly, Secure, SameSite=Strict cookie. The same-origin audio endpoint checks it
on every request and supports GET byte ranges and HEAD, streaming in 64 KB chunks.
It accepts neither browser-supplied paths nor remote URLs. Use the configured HTTPS
Reader origin; reopen Reader to refresh expired access. Missing audio falls back
to TTS, and direct podcast enclosure playback remains supported.

Validation includes mocked workflow, access/range/streaming tests and frontend
playback tests. A live metadata/range probe for `rHyuQctiDZM` succeeded; its Chinese
captions mapped 252 of 301 sentences exactly. This does not verify playback on a
phone. Before deployment acceptance, check Telegram WebView cookies, original
voice and sentence boundaries, rapid sentence switching, app backgrounding, and
reopening Reader after restart.
