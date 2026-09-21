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
