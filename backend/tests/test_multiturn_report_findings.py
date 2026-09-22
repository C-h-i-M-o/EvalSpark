"""报告失败与待判断项保留逐组原文依据，不把未知当失败。"""
from app.schemas.multiturn import CheckItem, Reassessment
from app.services.multiturn.assessments import _report_opportunities


def test_report_findings_keep_failed_unknown_and_invalid_distinct() -> None:
    """失败项、未知项和无效整组结果不能混为同一种失败。"""
    items = [
        CheckItem(id="bad", dimension="goal", rating=0, critical=True, passed=False, reason="未遵守预算",
                  evidence_refs=[{"source_id": "response:7", "turn": 3, "quote": "预算无限"}]),
        CheckItem(id="unknown", dimension="memory", applicability="unknown", reason="缺少判断依据"),
        CheckItem(id="good", dimension="efficiency", rating=4),
        CheckItem(id="na", dimension="correction", applicability="not_applicable"),
    ]
    result = _report_opportunities([Reassessment(run_index=1, valid=True, items=items),
                                   Reassessment(run_index=2, valid=False, items=items)])
    assert [item["id"] for item in result[0]["findings"]] == ["bad", "unknown"]
    assert result[0]["findings"][0]["evidence_refs"] == [{"source_id": "response:7", "turn": 3, "quote": "预算无限"}]
    assert result[0]["findings"][1]["applicability"] == "unknown"
    assert result[1]["findings"] is None


def test_coverage_audits_are_not_counted_as_conversation_opportunities() -> None:
    """无遗漏审核不是一次不适用机会，未知覆盖也单列，仍保留待判断明细。"""
    items = [CheckItem(id="goal", dimension="goal", rating=4),
             CheckItem(id="audit:1", dimension="goal", applicability="not_applicable"),
             CheckItem(id="audit:2", dimension="goal", applicability="unknown", reason="可能遗漏")]
    result = _report_opportunities([Reassessment(run_index=1, valid=True, items=items)],
                                   coverage_check_ids=frozenset({"audit:1", "audit:2"}))
    assert result[0]["dimensions"]["goal"] == {"opportunities": 1, "successful": 1, "unknown": 0,
        "notApplicable": 0, "failedCheckIds": [], "coverageChecked": 1, "coverageUnknown": 1}
    assert result[0]["findings"][0]["id"] == "audit:2"
