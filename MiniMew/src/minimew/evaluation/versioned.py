"""显式schema版本派发；不猜字段或静默降级，纯离线。"""

import json

from minimew.evaluation.gate import EvaluationArtifact
from minimew.evaluation.protocol import Corpus, EvaluationInputError, ObservationSet, load
from minimew.evaluation.result_protocol import CorpusV2
from minimew.evaluation.runtime_result import ArtifactV2, ObservationV2


def load_versioned(path, first, second):
    try:
        version = json.loads(path.read_text(encoding="utf-8")).get("schema_version", 1)
        if type(version) is not int or version not in (1, 2):
            raise EvaluationInputError("EVALUATION_SCHEMA_UNSUPPORTED")
    except EvaluationInputError:
        raise
    except (OSError, UnicodeError, ValueError, AttributeError):
        raise EvaluationInputError("INVALID_EVALUATION_INPUT") from None
    return load(path, first if version == 1 else second)


def load_corpus(path):
    return load_versioned(path, Corpus, CorpusV2)


def load_observations(path):
    return load_versioned(path, ObservationSet, ObservationV2)


def load_artifact(path):
    return load_versioned(path, EvaluationArtifact, ArtifactV2)
