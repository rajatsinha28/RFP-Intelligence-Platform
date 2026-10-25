from __future__ import annotations

import json
import re

from openai import AsyncOpenAI

from app.core.exceptions import ExternalServiceError


class QueryTransformer:
    def __init__(self, api_key: str | None, model: str, base_url: str | None = None) -> None:
        self.model = model
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url) if api_key else None

    @staticmethod
    def deterministic_variants(query: str) -> list[str]:
        normalized = " ".join(query.strip().split())
        variants = [normalized]
        stripped = re.sub(
            r"^(please|could you|can you|tell me|explain)\s+", "", normalized, flags=re.I
        )
        if stripped.lower() != normalized.lower():
            variants.append(stripped)
        clauses = [
            part.strip()
            for part in re.split(r"\b(?:and|versus|vs\.?|compared to)\b", normalized, flags=re.I)
        ]
        variants.extend(part for part in clauses if len(part.split()) >= 3)
        return list(dict.fromkeys(variants))[:4]

    async def transform(self, query: str, decompose: bool) -> list[str]:
        if not self._client:
            return self.deterministic_variants(query)
        schema = {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 4,
                }
            },
            "required": ["queries"],
            "additionalProperties": False,
        }
        instructions = (
            "Rewrite and decompose the user's knowledge-base question into at most four precise "
            "retrieval queries. Preserve names, dates, identifiers, and scope. Return the original "
            "query when rewriting is unnecessary. Do not answer the question."
            if decompose
            else "Rewrite the question into one concise retrieval query. Do not answer it."
        )
        try:
            response = await self._client.responses.create(
                model=self.model,
                instructions=instructions,
                input=query,
                max_output_tokens=300,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "retrieval_queries",
                        "strict": True,
                        "schema": schema,
                    }
                },
            )
            parsed = json.loads(response.output_text)
            queries = [" ".join(item.split()) for item in parsed["queries"] if item.strip()]
            return list(dict.fromkeys([query, *queries]))[:4]
        except Exception as exc:
            fallback = self.deterministic_variants(query)
            if fallback:
                return fallback
            raise ExternalServiceError("Query transformation failed") from exc

    async def close(self) -> None:
        if self._client:
            await self._client.close()
