from typing import Protocol

from openai import AsyncOpenAI, OpenAIError

from ..models import Chunk, Segment, Transcript, UserError


class Transcriber(Protocol):
    async def transcribe(
        self, chunk: Chunk, model: str, language: str | None, prompt: str
    ) -> Transcript: ...


class OpenAITranscriber:
    def __init__(self, client: AsyncOpenAI):
        self.client = client

    async def transcribe(
        self, chunk: Chunk, model: str, language: str | None, prompt: str
    ) -> Transcript:
        options = {"model": model, "prompt": prompt}
        if model == "gpt-transcribe":
            if language:
                options["extra_body"] = {"languages": [language]}
        else:
            if language:
                options["language"] = language
            options["response_format"] = "verbose_json" if model == "whisper-1" else "json"
        if model == "whisper-1":
            options["timestamp_granularities"] = ["segment"]
            options["prompt"] = prompt[:80]  # Stay conservatively below 224 Whisper tokens.
        try:
            with chunk.path.open("rb") as audio:
                response = await self.client.audio.transcriptions.create(file=audio, **options)
        except OpenAIError:
            raise UserError(
                "The OpenAI transcription request failed. Check your API access, balance, and model; use /retry when ready. A failed request may already have been billed."
            ) from None
        data = response.model_dump()
        if not data.get("text", "").strip():
            raise UserError("OpenAI returned an empty transcript. No completed result was cached.")
        segments = [
            Segment(float(s["start"]), float(s["end"]), s["text"].strip())
            for s in data.get("segments") or []
        ]
        detected = next(
            (x.get("code") for x in (data.get("languages") or []) if x.get("code")), None
        )
        detected = detected or {
            "chinese": "zh",
            "dutch": "nl",
            "english": "en",
            "german": "de",
            "french": "fr",
            "spanish": "es",
            "japanese": "ja",
        }.get(str(data.get("language", "")).lower())
        return Transcript(
            data["text"].strip(), segments, language or detected, data.get("usage") or {}
        )


def combine(parts: list[tuple[float, Transcript]]) -> Transcript:
    # Chunks do not overlap: preserve all recognized text, including intentional repetition.
    text = "\n\n".join(t.text.strip() for _, t in parts if t.text.strip()) + "\n"
    segments = [
        Segment(s.start + offset, s.end + offset, s.text) for offset, t in parts for s in t.segments
    ]
    return Transcript(text, segments, next((t.language for _, t in parts if t.language), None))


def srt_timestamp(seconds: float) -> str:
    ms = round(max(0, seconds) * 1000)
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def to_srt(transcript: Transcript) -> str:
    return (
        "\n\n".join(
            f"{i}\n{srt_timestamp(s.start)} --> {srt_timestamp(s.end)}\n{s.text}"
            for i, s in enumerate(transcript.segments, 1)
        )
        + "\n"
    )
