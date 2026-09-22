"""验证多轮 API 边界，拒绝客户端历史和不合法的评审配置。"""

import pytest
from pydantic import ValidationError

from app.schemas.conversation import ConversationCreate, TurnCreate


def test_conversation_defaults_private_and_rag_requires_knowledge() -> None:
    """新会话默认私有；RAG 必须明确知识库和评审模型。"""
    value = ConversationCreate(modelIds=[1], judgeModelId=2)
    assert value.visibility == "private" and value.mode == "chat"
    with pytest.raises(ValidationError):
        ConversationCreate(mode="rag", modelIds=[1], judgeModelId=2)
    assert ConversationCreate(mode="rag", modelIds=[1], judgeModelId=2, knowledgeBaseId=3).enable_thinking


@pytest.mark.parametrize("fields", [
    {"modelIds": []}, {"modelIds": [1, 1]}, {"modelIds": [True]},
    {"modelIds": [1], "judgeModelId": 1}, {"modelIds": [1], "messages": []},
    {"modelIds": [1], "inputBudget": 0},
])
def test_invalid_conversation_payload_is_rejected(fields: dict[str, object]) -> None:
    """配置不能重复、自我评审或注入客户端历史。"""
    with pytest.raises(ValidationError):
        ConversationCreate.model_validate(fields)


def test_turn_requires_exact_integer_and_idempotency_key() -> None:
    """轮次由后端管理，客户端只能提交问题、预期轮次与请求键。"""
    value = TurnCreate(prompt="继续", expectedTurn=0, requestKey="request-1")
    assert value.expected_turn == 0
    for invalid in ({"expectedTurn": True}, {"prompt": " "}, {"requestKey": ""}, {"history": []}):
        with pytest.raises(ValidationError):
            TurnCreate.model_validate({"prompt": "继续", "expectedTurn": 0, "requestKey": "request-1", **invalid})
