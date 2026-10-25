from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.rfp.schemas import REQUIRED_FIELDS


def _normalized(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True).lower()


def run_benchmark(gold_path: Path, artifact_dir: Path) -> dict[str, Any]:
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    details: list[dict[str, Any]] = []
    for bid_key, expectations in gold.items():
        artifact = json.loads((artifact_dir / f"{bid_key}.json").read_text(encoding="utf-8"))
        fields = artifact.get("fields", {})
        if set(expectations) != set(REQUIRED_FIELDS):
            raise ValueError(f"Gold fields for {bid_key} do not match the 20 required fields")
        for field_name in REQUIRED_FIELDS:
            expected = expectations[field_name]
            actual = fields.get(field_name, {})
            status_pass = actual.get("status") == expected["status"]
            value_text = _normalized(actual.get("value"))
            missing_keywords = [
                keyword for keyword in expected["keywords"] if keyword.lower() not in value_text
            ]
            citation_pass = (
                not actual.get("sources")
                if expected["status"] == "not_found"
                else bool(actual.get("sources"))
            )
            passed = status_pass and not missing_keywords and citation_pass
            details.append(
                {
                    "bid_key": bid_key,
                    "field": field_name,
                    "expected_status": expected["status"],
                    "actual_status": actual.get("status", "missing"),
                    "missing_keywords": missing_keywords,
                    "citation_pass": citation_pass,
                    "passed": passed,
                }
            )
    total = len(details)
    passed = sum(item["passed"] for item in details)
    citation_checks = [item for item in details if item["expected_status"] == "supported"]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "gold": str(gold_path),
        "field_count": total,
        "passed_fields": passed,
        "field_accuracy": passed / total if total else 0,
        "citation_pass_rate": (
            sum(item["citation_pass"] for item in citation_checks) / len(citation_checks)
            if citation_checks
            else 0
        ),
        "acceptance_threshold": 0.85,
        "quality_gate_pass": passed / total >= 0.85 if total else False,
        "failures": [item for item in details if not item["passed"]],
        "fields": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate canonical bid extraction artifacts")
    parser.add_argument("--gold", default="evaluation/datasets/rfp_extraction_gold.json")
    parser.add_argument("--artifacts", default="outputs/bids")
    parser.add_argument("--output", default="outputs/reports/rfp-extraction-quality.json")
    args = parser.parse_args()
    report = run_benchmark(Path(args.gold), Path(args.artifacts))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "field_accuracy": report["field_accuracy"],
                "citation_pass_rate": report["citation_pass_rate"],
                "quality_gate_pass": report["quality_gate_pass"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
