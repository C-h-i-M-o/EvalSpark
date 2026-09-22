import { afterEach, describe, expect, it, vi } from "vitest";
import { loadWorkspacePage, waitForAssessmentPoll } from "./workspaceData";
import * as api from "../../api/conversations";
import * as client from "../../api/client";

vi.mock("../../api/conversations");
vi.mock("../../api/client");
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

describe("多轮有界加载", () => {
  it("按指定页读取任务和新评分，不加载其他轮次", async () => {
    const signal = new AbortController().signal;
    vi.mocked(api.listConversationTurns).mockResolvedValue({ page: 3, pageSize: 10, total: 21,
      items: [{ id: 21, taskId: 99, turnIndex: 21, prompt: "最新问题", generationStatus: "completed", errorCode: null, createdAt: "now" }] });
    vi.mocked(client.getEvaluationTask).mockResolvedValue({ taskId: 99, status: "completed", prompt: "最新问题", ownerUsername: "测试", visibility: "private", responses: [] });
    const page = await loadWorkspacePage(1, 3, signal);
    expect(api.listConversationTurns).toHaveBeenCalledWith(1, 3, 10, signal);
    expect(client.getEvaluationTask).toHaveBeenCalledTimes(1);
    expect(client.getEvaluationTask).toHaveBeenCalledWith(99, signal);
    expect(page.turns[0].prompt).toBe("最新问题");
    expect(page.total).toBe(21);
  });

  it("取消轮询等待会结束 Promise 并清除定时器", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const wait = waitForAssessmentPoll(controller.signal);
    expect(vi.getTimerCount()).toBe(1);
    controller.abort();
    expect(await wait).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });
});
