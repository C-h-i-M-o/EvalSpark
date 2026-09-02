import pytest
from pydantic import ValidationError

from app.schemas.evaluation import EvaluationTaskCreate
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBasePatch, TextSource


def test_creation_normalizes_name_and_defaults_chunking() -> None:
    value = KnowledgeBaseCreate(name="  制度库  ")
    assert value.name == "制度库"
    assert value.chunk_size == 800
    assert value.chunk_overlap == 120


@pytest.mark.parametrize("values", [
    {"name": " "}, {"name": "库", "chunkSize": 127},
    {"name": "库", "chunkSize": 2049}, {"name": "库", "chunkOverlap": 800},
    {"name": "库", "chunkOverlap": -1}, {"name": "库", "chunkSize": True},
    {"name": "库", "userId": 99}, {"name": "库", "description": "a" * 2001},
])
def test_creation_rejects_invalid_chunking_and_client_ownership(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        KnowledgeBaseCreate(**values)


@pytest.mark.parametrize("values", [{"chunkSize": None}, {"name": None}, {"chunkOverlap": None}])
def test_patch_rejects_explicit_null_for_required_fields(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        KnowledgeBasePatch(**values)


def test_text_source_rejects_reversed_or_zero_based_line_ranges() -> None:
    for start, end in [(0, 1), (2, 1)]:
        with pytest.raises(ValidationError):
            TextSource(kind="text", lineStart=start, lineEnd=end)


def test_ordinary_requests_default_to_chat_and_unopened_rag_is_rejected() -> None:
    value = EvaluationTaskCreate(prompt="问题", modelIds=[1], enableJudge=False)
    assert value.task_type == "chat"
    with pytest.raises(ValidationError):
        EvaluationTaskCreate(prompt="问题", modelIds=[1], enableJudge=False, taskType="rag")
