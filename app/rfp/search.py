from __future__ import annotations

from collections.abc import Iterable

from app.query.transform import QueryTransformer

RFP_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "deadline": ("due date", "submission deadline", "closing date"),
    "due date": ("deadline", "closing date", "submission date"),
    "bond": ("bid bond", "security", "percentage"),
    "affidavit": ("affidavit", "certification", "required form"),
    "warranty": ("warranty", "extended warranty", "support term"),
    "model": ("model number", "model_no", "product model"),
    "part": ("part number", "part_no", "SKU"),
    "category": ("device category", "device type", "equipment tier", "evaluation pricing"),
    "pricing": ("evaluation pricing", "pricing table", "device category", "tier"),
    "quantities": (
        "anticipated equipment quantities",
        "one-time buys",
        "spread over life of the contract",
    ),
    "purchase": ("one-time buy", "contract term", "life of the contract"),
    "certification": ("certified", "qualification", "compliance requirement"),
    "submission": ("bid submission type", "electronic portal", "sealed bid", "email"),
}


def rfp_query_variants(query: str, base: Iterable[str] = ()) -> list[str]:
    variants = list(QueryTransformer.deterministic_variants(query))
    variants.extend(base)
    lowered = query.lower()
    expanded: list[str] = []
    for trigger, synonyms in RFP_EXPANSIONS.items():
        if trigger in lowered:
            expanded.append(f"{query} {' '.join(synonyms)}")
    return list(dict.fromkeys([*variants, *expanded]))[:4]
