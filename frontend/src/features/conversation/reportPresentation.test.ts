import { describe, expect, it } from "vitest";
import type { ConversationAssessmentDetailRead, ConversationRead } from "../../api/conversationTypes";
import { conversationModelOptions, reportMetadata, reviewPresentation } from "./reportPresentation";

const conversation: ConversationRead = { id: 1, title: "会话", mode: "chat", ownerId: 2, canContinue: true, visibility: "private", currentTurn: 3, generationStatus: "completed", configuration: { modelIds: [4, 8], judgeModelId: 9 }, createdAt: "", updatedAt: null };

describe("reportPresentation", () => {
  it("只从会话固定配置生成模型选项和截止轮次", () => {
    expect(conversationModelOptions([4, 8], new Map([[4, "模型甲"], [99, "无关模型"]]))).toEqual([{ value: 4, label: "模型甲" }, { value: 8, label: "模型 8" }]);
  });

  it("读取报告限制、失败轮次和成功次数", () => {
    expect(reportMetadata({ report: { limitations: ["跨窗口未联合判断"], failedGenerationTurns: [2], successfulResponses: 2 } })).toEqual({ limitations: ["跨窗口未联合判断"], failedGenerationTurns: [2], successfulResponses: 2 });
    expect(reportMetadata(null).successfulResponses).toBeNull();
  });

  it("解析详情评审序号并在元数据缺失时回退", () => {
    const detail = { ...conversation, modelConfigId: 4, throughTurn: 3, scoreVersion: "v1", status: "scored", formal: false, result: null, responseId: null, completedAt: null, runs: [{ runIndex: 1, status: "scored", result: { reviewIndex: 7, batchIndex: 2 }, errorCode: null }] } as unknown as ConversationAssessmentDetailRead;
    expect(reviewPresentation(detail, 1)).toEqual({ reviewIndex: 7, batchIndex: 2 });
    expect(reviewPresentation(detail, 3)).toEqual({ reviewIndex: 3, batchIndex: null });
  });
});
