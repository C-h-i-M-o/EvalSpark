import { describe, expect, it } from "vitest";
import { reportResolutions } from "./reportResolutions";

describe("报告问题后续状态", () => {
  it("旧报告与缺失结果不推断为没有问题", () => {
    expect(reportResolutions(null)).toEqual({ available: false, items: [] });
    expect(reportResolutions({ report: { issueResolutions: [] } }).available).toBe(false);
    expect(reportResolutions({ report: { resolutionVersion: 1, resolutionComplete: true, issueResolutions: [] } })).toEqual({ available: true, items: [] });
  });

  it("独立展示状态和有效组的原文，未知或无效组不伪装为已解决", () => {
    const result = { report: { resolutionVersion: 1, resolutionComplete: true, issueResolutions: [
      { issueId: "budget", description: "预算错误", status: "resolved", throughTurn: 3, reason: "一致",
        reviews: [{ reviewIndex: 1, valid: true, status: "resolved", reason: "已修正",
          evidence: [{ source_id: "response:2", turn: 2, quote: "预算100" }] },
          { reviewIndex: 2, valid: false, status: "resolved", evidence: [{ source_id: "response:2", turn: 2, quote: "预算100" }] }] },
      { issueId: "unknown", status: "unexpected", throughTurn: -1 },
    ] } };
    const view = reportResolutions(result);
    expect(view.items[0].state).toBe("已修正");
    expect(view.items[0].reviews[0].references[0]).toEqual({ sourceId: "response:2", turn: 2, quote: "预算100" });
    expect(view.items[0].reviews[1].state).toBe("无有效结论");
    expect(view.items[0].reviews[1].references).toEqual([]);
    expect(view.items[1].state).toBe("未知");
    expect(view.items[1].throughTurn).toBeNull();
  });
});
