"""要求提取验证原文、范围、修改与评分时间边界。"""
import json
from unittest.mock import AsyncMock

import pytest

from app.services.multiturn.requirements import Requirement, active_requirements, extract_requirements
from test_multiturn_judge import StubJudge


def old_requirements() -> tuple[Requirement, ...]:
    """创建持续预算限制和只适用旧轮次的格式要求。"""
    return (
        Requirement(id="budget", text="预算5000", quote="预算5000", source_id="u1", source_turn=1,
                    scope="conversation", critical=True, ambiguous=False),
        Requirement(id="format", text="本轮用表格", quote="本轮用表格", source_id="u1", source_turn=1,
                    scope="turn", critical=False, ambiguous=False),
    )


async def extract(proposals, *, previous=None):
    """固定来源与费用回调，返回完整提取结果。"""
    client = StubJudge(json.dumps({"requirements": proposals}, ensure_ascii=False))
    after = AsyncMock()
    result = await extract_requirements(client, source_id="u2", turn=2, prompt="预算改为7000，按你刚才说的方案",
        previous=old_requirements() if previous is None else previous, input_budget=10000, output_tokens=1000,
        before_call=AsyncMock(), after_call=after)
    return result, client, after


def proposal(**changes):
    """构造有明确用户原文依据的预算修改。"""
    return {"text": "预算7000", "quote": "预算改为7000", "scope": "conversation",
            "critical": True, "ambiguous": False, "supersedes": ["budget"], **changes}


@pytest.mark.asyncio
async def test_user_correction_supersedes_without_changing_past_scores() -> None:
    """第二轮改预算不会改写第一轮适用要求。"""
    result, client, after = await extract([proposal()])
    assert {item.id for item in active_requirements(result, 1)} == {"budget", "format"}
    current = active_requirements(result, 2)
    assert len(current) == 1 and current[0].text == "预算7000"
    assert current[0].source_id == "u2" and current[0].supersedes == ("budget",)
    assert "candidate" not in client.requests[0].messages[1].content
    after.assert_awaited_once()
    assert after.await_args.args[0].usage.total_tokens == 20


@pytest.mark.asyncio
async def test_empty_result_does_not_erase_existing_requirement() -> None:
    """无新增要求保留已有约束，旧轮次格式要求自然失效。"""
    result, _, _ = await extract([])
    assert result == old_requirements()
    assert [item.id for item in active_requirements(result, 2)] == ["budget"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"quote": "无限预算"}, {"supersedes": ["unknown"]}, {"supersedes": ["format"]},
    {"ambiguous": True}, {"critical": 1},
])
async def test_invalid_extraction_rejected(change) -> None:
    """伪造原文、失效引用、歧义撤销与非布尔类型不能进入评分要求。"""
    with pytest.raises(ValueError):
        await extract([proposal(**change)])


@pytest.mark.asyncio
async def test_ambiguous_reference_preserves_old_requirement() -> None:
    """未解析的模型方案指代只能保留歧义，不能撤销共享约束。"""
    result, _, _ = await extract([proposal(text="使用先前方案", quote="按你刚才说的方案",
                                         ambiguous=True, critical=False, supersedes=[])])
    assert len(active_requirements(result, 2)) == 2
    assert result[-1].ambiguous and not result[-1].critical
