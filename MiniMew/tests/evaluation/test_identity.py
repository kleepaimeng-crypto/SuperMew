"""共享来源目录的真实宿主与资格隔离。"""

from dataclasses import replace
from uuid import uuid4

import pytest

from minimew.evidence import EvidenceDirectory, EvidenceLedger
from minimew.execution.identity import CaseIdentity, ChatIdentity


def test_chat_adapter_keeps_existing_ownership_contract():
    user, thread, run = uuid4(), uuid4(), uuid4()
    ledger = EvidenceLedger(user, thread, run)
    assert ledger.owner == (user, thread, run)
    assert ledger.owns(user, thread, run)
    assert ledger.owned_by(ChatIdentity(user, thread, run))
    assert ledger.identity.checkpoint_key == str(run)


def test_case_attempts_and_chat_never_share_evidence_or_fake_run_identity():
    identity = CaseIdentity(uuid4(), uuid4(), uuid4(), uuid4())
    first, second = EvidenceDirectory(identity), EvidenceDirectory(identity)
    first.queried = True
    assert not second.queried
    assert not first.owned_by(replace(identity, case_id=uuid4()))
    successor = replace(identity, attempt_id=uuid4())
    assert not first.owned_by(successor)
    assert identity.checkpoint_key == successor.checkpoint_key
    assert not first.owned_by(ChatIdentity(identity.user_id, identity.job_id, identity.case_id))
    assert not hasattr(identity, "run_id")


@pytest.mark.parametrize("kind", ["chat", "case", "directory"])
def test_missing_or_untyped_identities_fail_closed(kind):
    with pytest.raises(ValueError):
        if kind == "chat":
            ChatIdentity(uuid4(), None, uuid4())
        elif kind == "case":
            CaseIdentity(uuid4(), uuid4(), "fake", uuid4())
        else:
            EvidenceDirectory((uuid4(), uuid4(), uuid4()))
