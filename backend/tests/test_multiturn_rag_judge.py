"""RAG Judge 只判定固定回答片段，不能控制引用关系和覆盖范围。"""
import copy
import json
from decimal import Decimal

import pytest

from app.services.multiturn.rag_judge import RagJudgeEvidence, build_rag_material, parse_rag_verdicts
from app.services.multiturn.scoring import score_rag_evidence
from app.services.multiturn.judge import JudgeCheck, JudgePacket, evaluate_packet
from test_multiturn_judge import StubJudge


def material():
    """冻结两个回答片段和唯一实际资料。"""
    return build_rag_material("试用期三个月。[S1][S99]\n谢谢。", (
        RagJudgeEvidence(id="response:1:S1", label="S1", text="试用期为三个月。"),
    ))


def verdicts() -> list[dict[str, object]]:
    """提供完整片段判定与真实资料引文。"""
    return [
        {"id": "segment:0", "needs_citation": True, "support": 1,
         "citation_support": 1, "reason": "资料支持三个月",
         "evidence": [{"source_id": "response:1:S1", "quote": "三个月"}],
         "citations": [{"label": "S1", "support": 1, "quote": "三个月"}]},
        {"id": "segment:21", "needs_citation": False, "support": None,
         "citation_support": None, "reason": "礼貌语", "evidence": [], "citations": []},
    ]


def payload() -> list[dict[str, object]]:
    """使用实际偏移生成有效固定 ID，避免中文长度假设。"""
    data = verdicts()
    for row, segment in zip(data, material().segments, strict=True):
        row["id"] = segment.id
    return data


def test_fixed_segments_keep_trailing_citations_and_offsets() -> None:
    """句末引用仍属于对应句，且每个片段能定位回答原文。"""
    snapshot = material()
    assert len(snapshot.segments) == 2
    first = snapshot.segments[0]
    assert first.citations == ("S1", "S99")
    assert snapshot.answer[first.start:first.end] == first.text
    assertions = parse_rag_verdicts(payload(), snapshot)
    assert assertions[0].invalid_cited_evidence_ids == ["S99"]
    assert "response:1:S1: 三个月" in assertions[0].evidence
    result = score_rag_evidence(list(assertions), snapshot.answer, {"S1"})
    assert result.final == Decimal("8.50")


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "quote", "source", "relation", "bool", "na_score", "coverage"])
def test_forged_or_incomplete_judge_data_is_rejected(mutation: str) -> None:
    """漏项、假引文、错来源、伪造关系和非法档位必须拒绝。"""
    data = copy.deepcopy(payload())
    if mutation == "missing":
        data.pop()
    elif mutation == "duplicate":
        data.append(data[0])
    elif mutation == "quote":
        data[0]["citations"][0]["quote"] = "六个月"
    elif mutation == "source":
        data[0]["evidence"][0]["source_id"] = "summary"
    elif mutation == "relation":
        data[0]["citations"].append({"label": "S99", "support": 1, "quote": "三个月"})
    elif mutation == "bool":
        data[0]["support"] = True
    elif mutation == "na_score":
        data[1]["support"] = 1
    else:
        data[0]["citation_support"] = 0
    with pytest.raises(ValueError):
        parse_rag_verdicts(data, material())


def test_unsupported_assertion_and_unknown_support_are_distinct() -> None:
    """确定不支持可给零，无法判断保留未知，不用假引文补齐。"""
    data = payload()
    data[0].update(support=None, citation_support=None, evidence=[],
                   citations=[{"label": "S1", "support": None, "quote": None}])
    assertions = parse_rag_verdicts(data, material())
    result = score_rag_evidence(list(assertions), material().answer, {"S1"})
    assert result.final is None
    assert result.faithfulness is None


def test_material_rejects_duplicate_source_labels() -> None:
    """重复资料标签不能由字典覆盖选择任意来源。"""
    with pytest.raises(ValueError):
        build_rag_material("回答[S1]", (
            RagJudgeEvidence(id="a", label="S1", text="甲"),
            RagJudgeEvidence(id="b", label="S1", text="乙"),
        ))


@pytest.mark.asyncio
async def test_one_call_returns_dialogue_and_rag_evidence_with_usage() -> None:
    """同一次调用产出两组分数，并保留可审计来源和真实用量。"""
    snapshot = material()
    packet = JudgePacket(scope="dialogue", through_turn=1, answer=snapshot.answer,
        sources=(), checks=(JudgeCheck(id="solution", dimension="solution", description="解决问题"),),
        rag=snapshot)
    output = {"items": [{"id": "solution", "applicability": "applicable", "rating": 4,
        "passed": None, "reason": "回答问题", "evidence": [{"source_id": "answer", "quote": "三个月"}]}],
        "rag": payload()}
    client = StubJudge(json.dumps(output, ensure_ascii=False))
    result = await evaluate_packet(client, packet, input_budget=20000)
    assert result.status == "provisional"
    assert result.rag_score.final == Decimal("8.50")
    assert result.rag_assertions[0].invalid_cited_evidence_ids == ["S99"]
    assert result.reply.usage.total_tokens == 20
    assert len(client.requests) == 1
    output["rag"].pop()
    failed = await evaluate_packet(StubJudge(json.dumps(output)), packet, input_budget=20000)
    assert failed.status == "judge_failed"
    assert failed.reply.usage.total_tokens == 20
    assert failed.rag_score is None
