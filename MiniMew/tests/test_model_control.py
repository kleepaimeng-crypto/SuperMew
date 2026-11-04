import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from minimew.model_control import (
    ModelConfiguration,
    ModelSnapshot,
    ModelSnapshotUnavailable,
    ProfileSnapshot,
    snapshot_for_run,
)
from minimew.models import Run


@pytest.mark.parametrize(
    "change",
    [
        {"api_key": "private-key"},
        {"headers": {"Authorization": "private-key"}},
        {"provider": "unknown"},
        {"thinking": {"type": "enabled"}},
        {"thinking": {}},
        {"model_name": ""},
        {"model_name": "bad\nname"},
        {"base_url": "https://user:private-key@models.example/v1"},
        {"base_url": "https://models.example/v1?api_key=private-key"},
        {"base_url": "https://models.example/v1#private-key"},
        {"base_url": "file:///tmp/model"},
        {"temperature": -0.1},
        {"temperature": 2.1},
        {"temperature": float("nan")},
        {"temperature": "0.2"},
        {"max_output_tokens": True},
        {"max_output_tokens": 0},
        {"timeout_seconds": 0},
    ],
)
def test_configuration_rejects_secrets_and_invalid_parameters(model_configuration, change):
    data = model_configuration.model_dump() | change
    with pytest.raises(ValidationError) as caught:
        ModelConfiguration.model_validate(data)
    assert "private-key" not in str(caught.value)


def test_snapshot_nested_configuration_is_frozen_and_hash_is_stable(model_configuration):
    snapshot = ModelSnapshot(
        answer=ProfileSnapshot(
            profile_id=uuid4(), profile_version=1, configuration=model_configuration
        )
    )
    data = snapshot.model_dump(mode="json")
    reordered = {key: data[key] for key in reversed(data)}
    restored = ModelSnapshot.model_validate_json(json.dumps(reordered))
    assert restored == snapshot and restored.catalog_hash() == snapshot.catalog_hash()
    with pytest.raises(ValidationError):
        snapshot.answer.configuration.temperature = 0.8
    with pytest.raises(ValidationError):
        snapshot.answer.profile_version = 2
    assert snapshot.answer.configuration.thinking.type == "disabled"
    with pytest.raises(ValidationError):
        snapshot.answer.configuration.thinking.type = "enabled"
    run = Run(model_snapshot_json=data, model_catalog_hash=snapshot.catalog_hash())
    assert snapshot_for_run(run) == snapshot
    data["answer"]["configuration"]["model_name"] = "different-model"
    assert snapshot.answer.configuration.model_name == "test-model-v1"
    with pytest.raises(ModelSnapshotUnavailable, match="MODEL_SNAPSHOT_INVALID"):
        snapshot_for_run(run)


@pytest.mark.parametrize("data,digest", [(None, None), ({}, None), ({}, "0" * 64)])
def test_missing_or_invalid_snapshot_is_never_replaced_from_current_config(data, digest):
    run = Run(model_snapshot_json=data, model_catalog_hash=digest)
    with pytest.raises(ModelSnapshotUnavailable) as caught:
        snapshot_for_run(run)
    assert caught.value.code == ("MODEL_SNAPSHOT_INVALID" if digest else "MODEL_SNAPSHOT_MISSING")


def test_local_cli_rejects_secret_without_echo_or_database_access(tmp_path, model_configuration):
    source = tmp_path / "invalid.json"
    source.write_text(
        json.dumps(model_configuration.model_dump() | {"api_key": "private-key"}), encoding="utf-8"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "minimew.model_cli",
            "set",
            "--name",
            "answer",
            "--file",
            str(source),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "Invalid configuration" in result.stderr
    assert "private-key" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
