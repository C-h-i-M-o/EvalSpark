import { afterEach, describe, expect, it, vi } from "vitest";
import { getConversationAssessment, listConversationAssessments, listConversationReports, streamConversationTurn, submitConversationReport, submitConversationReassessment } from "./conversations";
import type { ConversationTurnStreamEvent } from "./conversationTypes";

afterEach(() => vi.unstubAllGlobals());

describe("多轮会话协议", () => {
  it("报告提交仅发送冻结分支和截止轮次，查询支持取消与分页", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ items: [], id: 9 }), { status: 202 })));
    vi.stubGlobal("fetch", fetchMock);
    await submitConversationReport(7, 3, 42);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/evaluation/conversations/7/reports");
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ method: "POST", body: JSON.stringify({ modelConfigId: 3, throughTurn: 42 }) });
    const controller = new AbortController();
    await listConversationReports(7, 2, 10, controller.signal);
    expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/evaluation/conversations/7/reports?page=2&pageSize=10");
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ signal: controller.signal, credentials: "include" });
  });
  it("正式复评仅提交原评分标识，不提供客户端评分材料", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 9, status: "queued" }), { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await submitConversationReassessment(7, 8)).toMatchObject({ id: 9, status: "queued" });
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/evaluation/conversations/7/assessments");
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ method: "POST", credentials: "include", body: JSON.stringify({ sourceAssessmentId: 8 }) });
  });
  it("评分读取携带分页、回答筛选和取消信号", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], total: 0, page: 2, pageSize: 3 })));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    await listConversationAssessments(7, 2, 3, 42, controller.signal);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/evaluation/conversations/7/assessments?page=2&pageSize=3&responseId=42");
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ credentials: "include", signal: controller.signal });
  });

  it("评分权限错误保留 HTTP 状态", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "会话不存在" }), { status: 404 })));
    await expect(getConversationAssessment(7, 8)).rejects.toMatchObject({ status: 404 });
  });

  it("中文跨字节流和末尾无换行事件均完整解析", async () => {
    const bytes = new TextEncoder().encode([
      JSON.stringify({ type: "delta", modelConfigId: 2, delta: "你好" }),
      JSON.stringify({ type: "assessments_queued", turnId: 3, assessmentIds: [4] }),
    ].join("\n"));
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        // 逐字节分块覆盖中文 UTF-8 跨块以及最后一行没有换行的情况。
        for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body)));
    const events: ConversationTurnStreamEvent[] = [];
    await streamConversationTurn(7, { prompt: "继续", expectedTurn: 0, requestKey: "next" }, (event) => events.push(event));
    expect(events).toEqual([
      { type: "delta", modelConfigId: 2, delta: "你好" },
      { type: "assessments_queued", turnId: 3, assessmentIds: [4] },
    ]);
  });
});
