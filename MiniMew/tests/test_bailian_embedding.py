import pytest

from minimew.embeddings.bailian import _read_vectors
from minimew.embeddings.errors import EmbeddingError


class Response:
    def __init__(self, rows, model="text-embedding-v4"):
        self.rows, self.model = rows, model

    def json(self):
        return {"model": self.model, "data": self.rows}


def test_bulk_output_returns_normalized_vectors_in_input_order():
    vectors = _read_vectors(
        Response(
            [
                {"index": 1, "embedding": [0, 4] + [0] * 510},
                {"index": 0, "embedding": [2] + [0] * 511},
            ]
        ),
        2,
    )
    assert vectors == [[1, 0] + [0] * 510, [0, 1] + [0] * 510]


@pytest.mark.parametrize(
    ("index", "vector", "model"),
    [
        (0, [0] * 512, "text-embedding-v4"),
        (0, [float("nan")] + [0] * 511, "text-embedding-v4"),
        (0, [1, 0], "text-embedding-v4"),
        (1, [1] + [0] * 511, "text-embedding-v4"),
        (0, [1] + [0] * 511, "other-model"),
    ],
)
def test_invalid_output_fails_before_vector_publication(index, vector, model):
    with pytest.raises(EmbeddingError, match="EMBEDDING_INVALID_OUTPUT"):
        _read_vectors(Response([{"index": index, "embedding": vector}], model), 1)
