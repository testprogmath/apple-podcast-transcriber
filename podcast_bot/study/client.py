import asyncio
import json
import logging
from typing import Protocol, TypeVar

from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

from ..diagnostics import diagnostic
from ..models import UserError
from ..storage import Storage, now
from .settings import pricing

T = TypeVar("T", bound=BaseModel)
log = logging.getLogger(__name__)


class StructuredClient(Protocol):
    async def request(
        self, schema: type[T], instructions: str, payload: dict, model: str, max_output: int
    ) -> tuple[T, dict]: ...


class OpenAIStudyClient:
    def __init__(self, client: AsyncOpenAI):
        self.client = client

    async def request(
        self, schema: type[T], instructions: str, payload: dict, model: str, max_output: int
    ) -> tuple[T, dict]:
        try:
            response = await self.client.responses.parse(
                model=model,
                store=False,
                max_output_tokens=max_output,
                input=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                text_format=schema,
            )
        except (OpenAIError, ValidationError, ValueError) as exc:
            log.error("stage=study-api %s", diagnostic(exc))
            raise UserError(
                "Study generation failed or returned invalid data. Your transcript is saved. Use /retry; a failed request may have been billed."
            ) from None
        if response.status != "completed" or response.output_parsed is None:
            raise UserError(
                "The study model refused or did not finish its response. Your transcript is saved. Use /retry."
            )
        usage = response.usage.model_dump() if response.usage else {}
        return response.output_parsed, usage


class StudyRequests:
    """Persistent checkpoints for validated calls, separate from paid ASR usage."""

    def __init__(self, storage: Storage, client: StructuredClient):
        self.storage, self.client = storage, client

    async def call(
        self,
        *,
        job_id: int,
        key: str,
        step: str,
        model: str,
        schema: type[T],
        instructions: str,
        payload: dict,
        validate=None,
        max_output: int = 24000,
    ) -> T:
        db = self.storage.db
        cached = db.execute(
            "SELECT result FROM study_steps WHERE key=? AND step=?", (key, step)
        ).fetchone()
        if cached:
            result = schema.model_validate_json(cached[0])
            if validate:
                validate(result)
            return result
        if db.execute(
            "SELECT 1 FROM study_usage WHERE key=? AND step=? AND status='interrupted-unknown'",
            (key, step),
        ).fetchone():
            raise UserError(
                "A study API request was interrupted and may already be billed. Use /retry to authorize resuming; completed study chunks are saved."
            )
        with db:
            usage_id = db.execute(
                "INSERT INTO study_usage(job_id,key,step,model,input_chars,timestamp,status) VALUES (?,?,?,?,?,?,'started')",
                (
                    job_id,
                    key,
                    step,
                    model,
                    len(instructions) + len(json.dumps(payload, ensure_ascii=False)),
                    now(),
                ),
            ).lastrowid
        try:
            result, usage = await self.client.request(
                schema, instructions, payload, model, max_output
            )
            # Persist token accounting even if our semantic validation subsequently rejects output.
            input_tokens = usage.get("input_tokens")
            output_tokens = usage.get("output_tokens")
            cached_tokens = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
            rates = pricing(model)
            estimate = None
            if rates and input_tokens is not None and output_tokens is not None:
                estimate = (
                    max(0, input_tokens - cached_tokens) * rates[0]
                    + cached_tokens * rates[1]
                    + output_tokens * rates[2]
                ) / 1_000_000
            with db:
                db.execute(
                    "UPDATE study_usage SET input_tokens=?,cached_input_tokens=?,output_tokens=?,estimated_cost=? WHERE id=?",
                    (input_tokens, cached_tokens, output_tokens, estimate, usage_id),
                )
            if validate:
                validate(result)
            with db:
                db.execute(
                    "INSERT OR REPLACE INTO study_steps VALUES (?,?,?)",
                    (key, step, result.model_dump_json()),
                )
                db.execute("UPDATE study_usage SET status='success' WHERE id=?", (usage_id,))
            return result
        except asyncio.CancelledError:
            raise
        except Exception:
            with db:
                db.execute(
                    "UPDATE study_usage SET status='failure-possibly-billed' WHERE id=?",
                    (usage_id,),
                )
            raise
