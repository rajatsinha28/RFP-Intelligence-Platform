from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from app.core.circuit_breaker import AsyncCircuitBreaker
from app.core.exceptions import ConfigurationError, ExternalServiceError
from app.generation.prompts import GROUNDING_PROMPT, STRICT_RETRY_SUFFIX
from app.schemas import AnswerStatus, Claim, RetrievalHit, TokenUsage


@dataclass(slots=True)
class GeneratedAnswer:
    answer: str
    status: AnswerStatus
    claims: list[Claim]
    model_used: str
    token_usage: TokenUsage


class OpenAIGenerator:
    def __init__(
        self,
        api_key: str | None,
        fast_model: str,
        advanced_model: str,
        max_output_tokens: int,
        base_url: str | None = None,
        fast_input_cost_per_million: float = 0.0,
        fast_output_cost_per_million: float = 0.0,
        advanced_input_cost_per_million: float = 0.0,
        advanced_output_cost_per_million: float = 0.0,
    ) -> None:
        self.fast_model = fast_model
        self.advanced_model = advanced_model
        self.max_output_tokens = max_output_tokens
        self.fast_rates = (fast_input_cost_per_million, fast_output_cost_per_million)
        self.advanced_rates = (
            advanced_input_cost_per_million,
            advanced_output_cost_per_million,
        )
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url) if api_key else None
        self._breaker = AsyncCircuitBreaker("OpenAI responses")

    @staticmethod
    def _schema() -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["answered", "partially_answered", "insufficient_evidence"],
                },
                "claims": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "citation_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["text", "citation_ids"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["answer", "status", "claims"],
            "additionalProperties": False,
        }

    async def generate(
        self,
        query: str,
        hits: list[RetrievalHit],
        advanced: bool,
        strict_retry: bool = False,
    ) -> GeneratedAnswer:
        client = self._client
        if not client:
            raise ConfigurationError("OPENAI_API_KEY is required for answer generation")
        model = self.advanced_model if advanced else self.fast_model
        evidence = "\n\n".join(
            f'<evidence chunk_id="{hit.chunk_id}" source="{hit.source_name}" '
            f'page="{hit.page_number or ""}" section="{hit.section or ""}">\n'
            f"{hit.text}\n</evidence>"
            for hit in hits
        )
        instructions = GROUNDING_PROMPT + (STRICT_RETRY_SUFFIX if strict_retry else "")
        try:
            response = await self._breaker.call(
                lambda: client.responses.create(
                    model=model,
                    instructions=instructions,
                    input=f"QUESTION:\n{query}\n\nEVIDENCE:\n{evidence}",
                    max_output_tokens=self.max_output_tokens,
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": "grounded_answer",
                            "strict": True,
                            "schema": self._schema(),
                        }
                    },
                )
            )
            data = json.loads(response.output_text)
            usage = getattr(response, "usage", None)
            input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
            details = getattr(usage, "output_tokens_details", None)
            reasoning_tokens = int(getattr(details, "reasoning_tokens", 0) or 0)
            input_rate, output_rate = self.advanced_rates if advanced else self.fast_rates
            estimated_cost = (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
            return GeneratedAnswer(
                answer=str(data["answer"]).strip(),
                status=AnswerStatus(data["status"]),
                claims=[Claim.model_validate(item) for item in data["claims"]],
                model_used=model,
                token_usage=TokenUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    reasoning_tokens=reasoning_tokens,
                    total_tokens=input_tokens + output_tokens,
                    estimated_cost_usd=round(estimated_cost, 8),
                ),
            )
        except Exception as exc:
            raise ExternalServiceError(f"OpenAI generation failed: {type(exc).__name__}") from exc

    async def health_check(self) -> bool:
        client = self._client
        if not client:
            return False
        try:
            models = await self._breaker.call(
                lambda: asyncio.gather(
                    client.models.retrieve(self.fast_model),
                    client.models.retrieve(self.advanced_model),
                )
            )
            return len(models) == 2
        except Exception:
            return False

    async def close(self) -> None:
        if self._client:
            await self._client.close()
