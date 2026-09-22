"""确定性队列验收模型保留旧 RAG 协议并返回新多轮原文检查项。"""
import json
from uuid import uuid4

import httpx
import pytest

from integration.fake_models import app
from integration.fake_models import multiturn_verdict


@pytest.mark.asyncio
async def test_fake_multiturn_model_honors_fixed_unknown_and_source_scope() -> None:
    """范围白名单、未知项和关键要求都能形成生产解析器需要的固定结构。"""
    marker = uuid4().hex
    packet = {"scope": "session", "answer": marker, "sources": [{"id": "old", "text": "旧原文"}, {"id": "new", "text": "新原文"}],
        "checks": [{"id": "budget", "critical": True, "allowed_source_ids": ["new"]},
                   {"id": "unknown", "critical": False, "required_applicability": "unknown", "allowed_source_ids": []}]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/chat/completions", headers={"Authorization": "Bearer rag-acceptance-only"}, json={
            "model": "multiturn-judge", "messages": [{"role": "system", "content": "固定检查"}, {"role": "user", "content": json.dumps(packet)}]})
        assert response.status_code == 200
        items = json.loads(response.json()["choices"][0]["message"]["content"])["items"]
        assert items[0]["evidence"] == [{"source_id": "new", "quote": "新原文"}] and items[0]["passed"] is True
        assert items[1]["rating"] is None and items[1]["evidence"] == []
        assert (await client.get(f"/test/multiturn-calls/{marker}")).json()["calls"] == 1


@pytest.mark.asyncio
async def test_fake_legacy_judge_remains_compatible_and_requires_test_credential() -> None:
    """新增多轮模式不改变旧 RAG 评审结构，也不能绕过测试凭据校验。"""
    payload = {"model": "judge", "messages": [{"role": "system", "content": "评审"}, {"role": "user", "content": json.dumps({
        "question": "报销", "answer": "报销申请需要发票[S1]", "evidence": [{"label": "S1"}]})}]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/v1/chat/completions", json=payload)).status_code == 401
        result = await client.post("/v1/chat/completions", headers={"Authorization": "Bearer rag-acceptance-only"}, json=payload)
        assert result.status_code == 200
        assert json.loads(result.json()["choices"][0]["message"]["content"])["answerQuality"] == 8


def test_semantic_fake_discovery_and_required_citations() -> None:
    """提取与正式评审遵守同一原文关系，覆盖审核不得伪装为成功机会。"""
    sources = [{"id": "turn:1:user", "turn": 1, "text": "固定目标"},
               {"id": "response:1", "turn": 1, "text": "目标回答"}]
    discovery = json.loads(multiturn_verdict({"through_turn": 1, "sources": sources}))
    assert discovery["complete"] and discovery["opportunities"][0]["target"]["source_id"] == "response:1"
    packet = {"scope": "session", "answer": "固定目标", "sources": sources, "checks": [
        {"id": "coverage", "critical": False, "coverage_only": True},
        {"id": "goal", "critical": False, "required_source_ids": [source["id"] for source in sources]}]}
    items = json.loads(multiturn_verdict(packet))["items"]
    assert items[0]["applicability"] == "not_applicable" and items[0]["rating"] is None
    assert {item["source_id"] for item in items[1]["evidence"]} == {source["id"] for source in sources}
