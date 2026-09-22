import { describe, expect, it } from "vitest";
import { reportStatistics } from "./reportStatistics";

describe("报告统计展示", () => {
  it("原文覆盖完整不等于评分覆盖完整，生成率按固定轮次计算", () => {
    const view = reportStatistics({ coverage: "0.5", report: { sourceCoverage: "1", turnCount: 4, successfulResponses: 3 } });
    expect(view.generationRate).toBe("75%");
    expect(view.sourceCoverage).toBe("100%");
    expect(view.scoreCoverage).toBe("50%");
  });
  it("缺失、无分母和非法值均不显示为零", () => {
    expect(reportStatistics(null).generationRate).toBe("未知");
    expect(reportStatistics({ report: { turnCount: 0, successfulResponses: 0 } }).generationRate).toBe("未知");
    expect(reportStatistics({ coverage: "NaN", report: { sourceCoverage: 2, turnCount: 2, successfulResponses: 3 } })).toMatchObject({ generationRate: "未知", sourceCoverage: "未知", scoreCoverage: "未知" });
  });
  it("逐组机会保持独立，无效评审不冒充零机会", () => {
    const dimensions = { goal: { opportunities: 2, successful: 1, unknown: 1, notApplicable: 0, coverageChecked: 3, coverageUnknown: 1, failedCheckIds: ["requirement:5"] } };
    const view = reportStatistics({ report: { opportunityReviews: [
      { reviewIndex: 1, valid: true, dimensions }, { reviewIndex: 2, valid: true, dimensions },
      { reviewIndex: 3, valid: false, dimensions: null },
    ] } });
    expect(view.reviews).toHaveLength(3);
    expect(view.reviews[0]?.dimensions[0]).toMatchObject({ label: "目标完成", opportunities: "2", successful: "1", unknown: "1", failed: "requirement:5" });
    expect(view.reviews[1]?.dimensions[0]?.opportunities).toBe("2");
    expect(view.reviews[0]?.dimensions[0]).toMatchObject({ coverageChecked: "3", coverageUnknown: "1" });
    expect(view.reviews[0]?.dimensions[1]?.coverageChecked).toBe("未知");
    expect(view.reviews[2]).toMatchObject({ valid: false, dimensions: [] });
  });
  it("RAG 零分、暂定分与未评分轮次保留各自含义", () => {
    const view = reportStatistics({ report: { ragEvidenceTrend: [
      { turn: 1, status: "scored", evidence: { final: "0", coverage: "1", status: "scored" } },
      { turn: 2, status: "provisional", evidence: { final: "8", coverage: "0.5" } },
      { turn: 3, status: "not_assessed", evidence: null },
      { turn: 4, status: "judge_failed", evidence: { final: null } },
    ] } });
    expect(view.trend[0]).toMatchObject({ final: "0.00 / 10", coverage: "100%", status: "正式评分" });
    expect(view.trend[1]).toMatchObject({ final: "8.00 / 10", status: "暂定评分" });
    expect(view.trend[2]).toMatchObject({ final: "未知", status: "未评分" });
    expect(view.trend[3]).toMatchObject({ final: "未知", status: "评审失败" });
  });
});
