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
