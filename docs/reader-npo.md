# NPO Start subtitle import

Paste a public HTTPS `npo.nl/start/afspelen/<slug>` episode URL into **New text**.
The existing documents POST endpoint accepts `{"source_type":"npo","url":"…"}`.
Authenticated ownership, library, rename, reading position and translation are reused.
Chinese text/file imports retain their existing behavior.

The adapter fetches public HTML, requires exactly one distinct `VPWON_` identifier
with 1–20 ASCII digits (a defensive length cap, not an asserted NPO format), and fetches `https://cdn.npoplayer.nl/subtitles/nl/{id}.vtt`.
Only npo.nl/www.npo.nl episode paths are accepted, without query/fragment/userinfo.
Each page redirect is validated before fetching; subtitle redirects are rejected.
Requests have a 20-second timeout and a 2 MiB decoded response limit.
No player API, authentication tokens, media downloading or playback is involved.

SQLite adds one backward-compatible `source_data TEXT NOT NULL DEFAULT '{}'`
column. JSON retains language, URLs, product ID, entire original VTT, cue text,
settings, classes, identifiers, classifications and normalized sentence spans/times.
Existing documents default to Chinese. NPO identity uses owner plus product ID;
repeat imports update content/source data while preserving existing title/created
and reading state, consistent with the existing upsert flow.

Normalization joins display lines and strips markup. Only the initial `888` cue
within two seconds is classified as TT888. A narrow set of parenthesized sound
labels and standalone music symbols is excluded while remaining in source data.
Parenthetical dialogue, later numeric dialogue and lyrics are retained.
The exact sound-label list also includes `(het Wilhelmus speelt)`, `(schoten)`
and `(taptoe-signaal)`. TT888 lyric boundary `*` markers are removed from visible
text when detached or leading a capitalized multiword lyric; ordinary internal
asterisks and lowercase emphasis are retained. Raw cue text keeps every marker.

Adjacent speech may join only with a nonnegative gap of at most one second,
lowercase continuation, unchanged presentation classes, no explicit speaker/dash
or music marker, no prior sentence terminator, and a 3000-character bound.
Class changes are conservative split evidence, never inferred speaker identities.
Trailing/leading ellipses are cleaned only when such a continuation is joined.
Explicit ellipsis enumerations may bridge gaps up to 2.00 seconds, based on
the observed 1.68/2.00-second gaps. The next item must also end in an ellipsis
or start with `en `; all other continuation guards still apply. A longer-gap
intermediate enumeration receives a comma; a final `en ` item receives a space.
Music-marked cues block joining even after visible markers are removed.
Other ellipses remain. Non-speech cues force a split. Sentence punctuation followed
by uppercase text splits within a group. Timings cover the first through last
contributing cue; multiple sentences inside one cue share its range.

This intentionally favors false splits. Unmarked speaker changes cannot always
be detected; abbreviations, lowercase sentence starts, ellipses, simultaneous
captions and mixed music/dialogue can remain ambiguous. No Dutch dictionary or
pinyin is provided. Dutch text bypasses Chinese segmentation/enrichment, exposes
existing sentence translation, and hides Mandarin speech/pinyin controls.
The parser supports ordinary timed WebVTT cues, optional cue IDs and settings,
and skips NOTE/STYLE/REGION blocks; it is not a general WebVTT renderer.
No additional dependency was needed. Tests use a minimal synthetic fixture and
mocked HTTP; live NPO availability is not part of validation.
