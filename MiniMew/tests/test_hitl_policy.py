from uuid import uuid4

import pytest

from minimew.hitl.policy import HitlPolicy
from minimew.model_control import ModelSnapshot, ProfileSnapshot


def test_v5_freezes_hitl_without_changing_v4_hash(model_configuration):
    from minimew.rag.protocol import RagPolicy
    from minimew.rag.reasoning import ReasoningPolicy, RepairPolicy

    v4 = ModelSnapshot(
        schema_version=4,
        answer=ProfileSnapshot(
            profile_id=uuid4(), profile_version=1, configuration=model_configuration
        ),
        rag=RagPolicy(),
        reasoning=ReasoningPolicy(),
        repairs=RepairPolicy(),
    )
    v5 = v4.model_copy(update={"schema_version": 5, "hitl": HitlPolicy()})
    assert ModelSnapshot.model_validate_json(v5.model_dump_json()).hitl.wait_seconds == 900
    assert v4.catalog_hash() != v5.catalog_hash()
    assert "hitl" not in v4.model_dump(exclude_none=True)
    assert (
        v4.catalog_hash()
        == v5.model_copy(update={"schema_version": 4, "hitl": None}).catalog_hash()
    )
    with pytest.raises(ValueError):
        ModelSnapshot.model_validate_json(
            v4.model_copy(update={"hitl": HitlPolicy()}).model_dump_json()
        )
