import json
from pathlib import Path

import pytest

from evaluation.unseen_audit import load_samples, validate_dataset_novelty


def _write(path: Path, questions: list[str]) -> None:
    samples = [
        {
            "id": str(index),
            "bid_key": "package-a",
            "question": question,
            "expected_answer": "value",
            "expected_citations": [],
            "expected_statuses": ["answered"],
            "negative_control": False,
        }
        for index, question in enumerate(questions)
    ]
    path.write_text(json.dumps({"samples": samples}), encoding="utf-8")


def test_novelty_rejects_normalized_duplicates(tmp_path: Path) -> None:
    dataset = tmp_path / "audit.json"
    _write(dataset, ["What is required?", "  WHAT   is required? "])
    with pytest.raises(ValueError, match="Duplicate questions"):
        validate_dataset_novelty(dataset, tmp_path)


def test_novelty_rejects_questions_in_sibling_dataset(tmp_path: Path) -> None:
    dataset = tmp_path / "audit.json"
    _write(dataset, ["Which form is required?"])
    _write(tmp_path / "existing.json", ["which form is required?"])
    with pytest.raises(ValueError, match="other datasets"):
        validate_dataset_novelty(dataset, tmp_path)


def test_load_samples_accepts_versioned_dataset_object(tmp_path: Path) -> None:
    dataset = tmp_path / "audit.json"
    _write(dataset, ["When is delivery due?"])
    assert load_samples(dataset)[0]["question"] == "When is delivery due?"
