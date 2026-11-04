import json
import runpy
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from minimew.execution.context import ContextInvalid, build_messages
from minimew.generated.run_event_v1 import EVENT_ADAPTER

ROOT = Path(__file__).resolve().parents[1]


def test_input_history_uses_complete_recent_pairs_and_keeps_current():
    messages = build_messages("now", [("new", "answer"), ("old", "response")], max_chars=12)
    assert [(m.role, m.content) for m in messages] == [
        ("user", "new"),
        ("assistant", "answer"),
        ("user", "now"),
    ]
    assert [m.content for m in build_messages("now", [("new", "answer"), ("old", "response")])] == [
        "old",
        "response",
        "new",
        "answer",
        "now",
    ]
    assert [
        m.content
        for m in build_messages("now", [("large", "x" * 100), ("tiny", "a")], max_chars=10)
    ] == ["now"]


@pytest.mark.parametrize(
    "current", ["", " ", "a\x00b", "x" * 60001], ids=["empty", "blank", "nul", "oversize"]
)
def test_invalid_current_input_fails_closed(current):
    with pytest.raises(ContextInvalid):
        build_messages(current, [])


@pytest.fixture
def event():
    return {
        "schema_version": 1,
        "event_id": str(uuid4()),
        "sequence": 1,
        "run_id": str(uuid4()),
        "thread_id": str(uuid4()),
        "type": "run.failed",
        "timestamp": datetime.now(UTC).isoformat(),
        "data": {"status": "failed", "error_code": "PROVIDER_TIMEOUT"},
    }


def test_event_contract_round_trip(event):
    parsed = EVENT_ADAPTER.validate_json(json.dumps(event))
    assert EVENT_ADAPTER.validate_json(EVENT_ADAPTER.dump_json(parsed)) == parsed


@pytest.mark.parametrize("content", ["今天", " ", "x" * 4096])
def test_delta_contract_accepts_bounded_text_batches(event, content):
    event.update(type="message.delta", data={"message_id": str(uuid4()), "content": content})
    parsed = EVENT_ADAPTER.validate_json(json.dumps(event))
    assert parsed.data.content == content


@pytest.mark.parametrize(
    "data",
    [
        {"message_id": str(uuid4()), "content": ""},
        {"message_id": str(uuid4()), "content": "x" * 4097},
        {"message_id": "wrong", "content": "text"},
        {"message_id": str(uuid4()), "content": "text", "secret": "private"},
    ],
)
def test_delta_contract_refuses_invalid_payload(event, data):
    with pytest.raises(ValidationError):
        EVENT_ADAPTER.validate_json(json.dumps(event | {"type": "message.delta", "data": data}))


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"sequence": 0},
        {"sequence": True},
        {"timestamp": "2026-10-30T12:00:00"},
        {"run_id": "not-a-uuid"},
        {"type": "unknown"},
        {"data": {"status": "failed", "error_code": "raw-private-error"}},
        {"data": {"status": "succeeded"}},
        {"data": {"status": "failed", "error_code": "PROVIDER_TIMEOUT", "api_key": "private"}},
    ],
)
def test_invalid_event_is_rejected(event, change):
    with pytest.raises(ValidationError):
        EVENT_ADAPTER.validate_json(json.dumps(event | change))


def test_generator_is_current_and_refuses_unhandled_schema_keywords():
    subprocess.run(
        [sys.executable, "scripts/generate_event_contract.py", "--check"],
        cwd=ROOT,
        check=True,
        timeout=15,
    )
    render = runpy.run_path(str(ROOT / "scripts/generate_event_contract.py"))["render"]
    schema = json.loads((ROOT / "contracts/run_event_v1.json").read_text(encoding="utf-8"))
    invalid = deepcopy(schema)
    invalid["properties"]["sequence"]["multipleOf"] = 2
    with pytest.raises(ValueError, match="Unsupported field keywords"):
        render(invalid)
    invalid = deepcopy(schema)
    invalid["$defs"]["RunStartedData"]["additionalProperties"] = True
    with pytest.raises(ValueError, match="closed"):
        render(invalid)
    invalid = deepcopy(schema)
    invalid["required"].remove("data")
    with pytest.raises(ValueError, match="must be required"):
        render(invalid)
