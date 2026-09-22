import { describe, expect, test } from "vitest";

import { conversationStatusText, formatHistoryTime, historyStatusText, isStalePendingTask, updateTaskResponseFeedback } from "./history";
import type { EvaluationTaskRead, EvaluationTaskListItem } from "../evaluation/types";

describe("React 阶段四历史任务逻辑", () => {
  test("公开会话对非作者显示只读状态", () => {
    expect(conversationStatusText(false, "idle")).toBe("只读");
    expect(conversationStatusText(true, "idle")).toBe("可继续");
  });
  test("RAG 使用 60 分钟收尾边界，不套用普通任务两分钟", () => {
    const task = { taskType: "rag" as const, status: "pending", createdAt: "2026-09-03T00:00:00" };
    expect(isStalePendingTask(task, new Date("2026-09-03T00:03:00Z"))).toBe(false);
    expect(isStalePendingTask(task, new Date("2026-09-03T01:00:00Z"))).toBe(true);
  });
  test("进行中任务超过等待阈值后显示为超时未完成", () => {
    const task: EvaluationTaskListItem = {
      taskId: 1,
      status: "pending",
      prompt: "问题",
      createdAt: "2026-07-03T04:00:00",
      completedAt: null,
      responseCount: 0,
      ownerId: 2,
      ownerUsername: "demo",
      visibility: "private"
    };

    expect(isStalePendingTask(task, new Date("2026-07-03T04:03:00Z"))).toBe(true);
    expect(historyStatusText(task, new Date("2026-07-03T04:03:00Z"))).toBe("超时未完成");
  });

  test("反馈结果可以同步到历史详情回答", () => {
    const task: EvaluationTaskRead = {
      taskId: 9,
      status: "completed",
      prompt: "历史问题",
      ownerUsername: "demo",
      visibility: "public",
      responses: [
        {
          id: 11,
          modelConfigId: 2,
          modelName: "DeepSeek",
          provider: "deepseek",
          answer: "答案",
          latencyMs: 100,
          inputTokens: 1,
          outputTokens: 2,
          cacheHitTokens: 0,
          cacheCreationTokens: 0,
          totalTokens: 3,
          estimatedCost: 0,
          currency: "CNY",
          costDetails: { inputCost: 0, outputCost: 0, cacheHitCost: 0, cacheCreationCost: 0 },
          status: "success",
          score: { relevance: 8, completeness: 8, clarity: 8, format: 8, safety: 8, final: 8, details: {} },
          feedback: { liked: false, likeCount: 0, disliked: false, dislikeCount: 0 }
        }
      ]
    };

    expect(
      updateTaskResponseFeedback(task, {
        responseId: 11,
        feedbackType: "like",
        active: true,
        feedback: { liked: true, likeCount: 1, disliked: false, dislikeCount: 0 },
        score: { relevance: 8, completeness: 8, clarity: 8, format: 8, safety: 8, final: 8.2, details: {} }
      }).responses[0]
    ).toMatchObject({
      feedback: { liked: true, likeCount: 1 },
      score: { final: 8.2 }
    });
  });

  test("后端无时区时间按 UTC 解析并格式化为北京时间", () => {
    expect(formatHistoryTime("2026-07-03T04:00:00")).toContain("2026");
  });
});
