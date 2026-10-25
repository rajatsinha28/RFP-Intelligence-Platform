import argparse
import json
from pathlib import Path
from typing import Any

from app import cli


def test_folder_import_discovers_unseen_bid_without_hard_coding(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    unseen = tmp_path / "NorthCounty-2027-Laptops"
    unseen.mkdir()
    (unseen / "RFP-27-44.pdf").write_bytes(b"synthetic")
    (tmp_path / "Assignment.pdf").write_bytes(b"instructions")
    imported: list[str] = []

    def capture(_client: object, args: argparse.Namespace) -> None:
        imported.append(args.bid_id)

    monkeypatch.setattr(cli, "_ingest_bid", capture)
    cli._ingest_folder(
        object(),
        argparse.Namespace(path=str(tmp_path), knowledge_base="rfp-intelligence"),
    )

    output = json.loads(capsys.readouterr().out)
    assert imported == ["NorthCounty-2027-Laptops"]
    assert output["ignored_root_files"] == ["Assignment.pdf"]
