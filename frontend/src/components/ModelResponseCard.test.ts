import { describe, expect, test } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { ModelResponse } from "../features/evaluation/types";

import { JUDGE_SCORE_WEIGHT_LABEL, JUDGE_STABILITY_THRESHOLD_LABEL, isNearScrollBottom, scoreStatusText, ModelResponseCard } from "./ModelResponseCard";

describe("ModelResponseCard 自动滚动判断", () => {
  test("多轮评分覆盖旧单轮评分栏但保留回答及反馈", () => {
    const response: ModelResponse = { id: 1, modelConfigId: 1, modelName: "候选", provider: "测试", answer: "回答内容",
      latencyMs: 10, inputTokens: 1, outputTokens: 1, cacheHitTokens: 0, cacheCreationTokens: 0, totalTokens: 2,
      estimatedCost: 0, currency: "CNY", costDetails: { inputCost: 0, outputCost: 0, cacheHitCost: 0, cacheCreationCost: 0 },
      status: "success", score: { relevance: 9, completeness: 9, clarity: 9, format: 9, safety: 9, final: null, details: {}, scoreStatus: "judge_disabled" },
      feedback: { liked: false, disliked: false, likeCount: 0, dislikeCount: 0 } };
    const html = renderToStaticMarkup(createElement(ModelResponseCard, { response, elapsedSeconds: 0,
      feedbackSubmitting: false, onFeedback: () => undefined, assessmentContent: createElement("p", null, "上下文理解：8.00") }));
    expect(html).toContain("上下文理解：8.00");
    expect(html).toContain("回答内容");
    expect(html).toContain("点赞");
    expect(html).not.toContain("本次关闭 LLM 评分");
    expect(html).not.toContain("相关性");
  });
  test("视口接近底部时允许自动滚动", () => {
    expect(isNearScrollBottom({ scrollTop: 460, clientHeight: 500, scrollHeight: 980 })).toBe(true);
  });

  test("用户向上查看内容时不自动滚动", () => {
    expect(isNearScrollBottom({ scrollTop: 120, clientHeight: 500, scrollHeight: 980 })).toBe(false);
  });

  test("五种评分状态都有明确文案", () => {
    expect(scoreStatusText("scored")).toBe("已计入统计");
    expect(scoreStatusText("judge_failed")).toContain("LLM 评分失败");
    expect(scoreStatusText("judge_unstable")).toContain("有效评分分歧较大");
    expect(scoreStatusText("judge_disabled")).toContain("关闭 LLM 评分");
    expect(scoreStatusText("model_failed")).toContain("模型调用失败");
  });

  test("LLM 评审详情展示 70% 权重", () => {
    expect(JUDGE_SCORE_WEIGHT_LABEL).toBe("70%");
  });

  test("LLM 评审详情展示 2.0 稳定阈值", () => {
    expect(JUDGE_STABILITY_THRESHOLD_LABEL).toBe("2.0");
  });
});
