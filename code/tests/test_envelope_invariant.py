"""Req 3 AC 6; Req 5 AC 1, 2, 4: every JSON artifact has the metadata envelope.

This is the suite-level invariant: regardless of which artifact future
phases add to ``out/``, the top-level shape MUST be ``{metadata, data}``
and the metadata MUST carry all five fields from Req 5 AC 2.
"""

import json
from datetime import date
from pathlib import Path

from code.pipeline.reconcile import run_layer1

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

REQUIRED_METADATA_FIELDS = {
    "ruleset_version",
    "code_commit",
    "input_file_sha256s",
    "as_of_date",
    "generated_at",
}


def test_every_json_artifact_has_metadata_envelope(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    run_layer1(PROJECT_ROOT, out_dir=out_dir, as_of_date=date(2026, 1, 2))

    json_files = sorted(out_dir.glob("*.json"))
    assert len(json_files) >= 2, "Expected at least raw_breaks.json + data_quality.json"

    for path in json_files:
        obj = json.loads(path.read_text())
        assert set(obj.keys()) == {"metadata", "data"}, (
            f"{path.name} top level must be {{metadata, data}}, got {sorted(obj.keys())}"
        )

        meta = obj["metadata"]
        missing = REQUIRED_METADATA_FIELDS - set(meta.keys())
        assert not missing, f"{path.name} metadata missing fields: {missing}"

        for field in REQUIRED_METADATA_FIELDS:
            value = meta[field]
            assert value not in (None, "", [], {}), (
                f"{path.name} metadata field {field!r} must be non-empty, got {value!r}"
            )

        assert isinstance(meta["input_file_sha256s"], dict)
        assert len(meta["input_file_sha256s"]) >= 1

        commit = meta["code_commit"]
        assert commit == "uncommitted" or (
            len(commit) >= 7 and all(c in "0123456789abcdef" for c in commit)
        ), f"code_commit must be a hex SHA or 'uncommitted', got {commit!r}"

        assert isinstance(obj["data"], list)
