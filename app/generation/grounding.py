from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas import Claim, RetrievalHit

TOKEN = re.compile(r"[A-Za-z0-9_'-]+")


@dataclass(slots=True)
class GroundingResult:
    valid: bool
    unsupported_claims: list[str]
    unknown_citations: list[str]


def validate_grounding(
    claims: list[Claim], hits: list[RetrievalHit], minimum_overlap: float = 0.12
) -> GroundingResult:
    by_id = {hit.chunk_id: hit for hit in hits}
    unsupported: list[str] = []
    unknown: list[str] = []
    for claim in claims:
        missing = [chunk_id for chunk_id in claim.citation_ids if chunk_id not in by_id]
        unknown.extend(missing)
        evidence = " ".join(by_id[item].text for item in claim.citation_ids if item in by_id)
        claim_tokens = {token.lower() for token in TOKEN.findall(claim.text) if len(token) > 2}
        evidence_tokens = {token.lower() for token in TOKEN.findall(evidence) if len(token) > 2}
        overlap = len(claim_tokens & evidence_tokens) / max(1, len(claim_tokens))
        if not claim.citation_ids or overlap < minimum_overlap:
            unsupported.append(claim.text)
    return GroundingResult(not unsupported and not unknown, unsupported, unknown)
