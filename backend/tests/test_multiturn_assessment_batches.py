"""分段报告必须覆盖完整固定检查项和原文，不能通过分段省略不利材料。"""
import pytest

from app.services.multiturn.context import estimate_tokens
from app.services.multiturn.judge import JudgeCheck, JudgePacket, JudgeSource, build_judge_messages
from app.services.multiturn.assessment_batches import validate_batches


def packets() -> tuple[JudgePacket, tuple[JudgePacket, ...]]:
    """构造两个分段共享目标原文、各自检查不同机会的报告。"""
    sources = (JudgeSource(id="u1", turn=1, text="原目标"), JudgeSource(id="a2", turn=2, text="回答原文"))
    checks = (JudgeCheck(id="g1", dimension="goal", description="目标机会"),
              JudgeCheck(id="m2", dimension="memory", description="保持机会"))
    parent = JudgePacket(scope="session", through_turn=2, answer="报告截至第二轮", sources=sources, checks=checks)
    return parent, (parent.model_copy(update={"checks": checks[:1], "sources": sources[:1]}),
                    parent.model_copy(update={"checks": checks[1:]}))


def test_batch_partition_preserves_checks_and_shared_sources() -> None:
    """共享原文允许重复读取，固定检查项必须只评价一次。"""
    parent, batches = packets()
    validate_batches(parent, batches, 10000)


@pytest.mark.parametrize("mutation", ["missing_check", "duplicate_check", "missing_source", "changed_source", "future", "answer", "dialogue", "empty"])
def test_invalid_batch_partition_is_rejected(mutation: str) -> None:
    """缺失、篡改及普通单轮误用分段都在收费前拒绝。"""
    parent, batches = packets()
    if mutation == "missing_check":
        batches = batches[:1]
    elif mutation == "duplicate_check":
        batches = (*batches, batches[0])
    elif mutation == "missing_source":
        batches = (batches[0], batches[1].model_copy(update={"sources": parent.sources[:1]}))
    elif mutation == "changed_source":
        batches = (batches[0], batches[1].model_copy(update={"sources": (parent.sources[1].model_copy(update={"text": "伪造"}),)}))
    elif mutation == "future":
        batches = (batches[0], batches[1].model_copy(update={"through_turn": 3}))
    elif mutation == "answer":
        batches = (batches[0], batches[1].model_copy(update={"answer": "其他目标"}))
    elif mutation == "dialogue":
        parent = parent.model_copy(update={"scope": "dialogue"})
    else:
        batches = ()
    with pytest.raises(ValueError):
        validate_batches(parent, batches, 10000)


def test_batch_budget_counts_actual_judge_system_and_json() -> None:
    """预算使用实际提示词和完整序列化数据，不能只按原文字数估算。"""
    parent, batches = packets()
    budget = max(estimate_tokens(build_judge_messages(batch)) for batch in batches)
    validate_batches(parent, batches, budget)
    with pytest.raises(ValueError, match="预算"):
        validate_batches(parent, batches, budget - 1)
