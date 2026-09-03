import { afterEach, expect, test, vi } from "vitest";
import { streamEvaluationTask, listEvaluationTasks, ApiError } from "./client";
import { buildRagPayload } from "../features/rag/rag";
afterEach(() => vi.unstubAllGlobals());
test("实际发送 RAG 私有请求并转发取消信号", async () => {
  const response = new Response('{"type":"rag_stage","modelConfigId":1,"stage":"rewriting"}\n');
  const fetchMock = vi.fn().mockResolvedValue(response); vi.stubGlobal("fetch", fetchMock);
  const signal = new AbortController().signal;
  const payload = buildRagPayload({ prompt: "问题", modelIds: [1], judgeModelId: 2, enableThinking: false,
    library: { id: 1, name: "库", description: null, chunkSize: 800, chunkOverlap: 120, status: "ready", available: true,
      documentCount: 1, chunkCount: 1, contentRevision: 1, errorCode: null, createdAt: "", updatedAt: "" } });
  const events: unknown[] = [];
  await streamEvaluationTask(payload, (event) => events.push(event), signal);
  const init = fetchMock.mock.calls[0][1] as RequestInit;
  expect(JSON.parse(String(init.body))).toMatchObject({ taskType: "rag", visibility: "private", enableJudge: true, judgeModelId: 2 });
  expect(init.signal).toBe(signal);
  expect(events).toEqual([{ type: "rag_stage", modelConfigId: 1, stage: "rewriting" }]);
  expect(response.body?.locked).toBe(false);
});
test("解析错误也释放读取器", async () => {
  const cancelled = vi.fn();
  const body = new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(new TextEncoder().encode("非 JSON\n")); }, cancel: cancelled });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body)));
  await expect(streamEvaluationTask({ prompt: "问题", modelIds: [1], enableJudge: true, judgeModelId: 2, enableThinking: false, visibility: "private" }, () => undefined)).rejects.toThrow();
  expect(cancelled).toHaveBeenCalledOnce(); expect(body.locked).toBe(false);
});
test("类型筛选在 API 请求中生效，领域错误显示安全 message", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: { code: "knowledge_base_not_ready", message: "知识库尚未就绪" } }), { status: 409 }));
  vi.stubGlobal("fetch", fetchMock);
  await expect(listEvaluationTasks({ page: 1, pageSize: 10, taskType: "rag" })).rejects.toEqual(new ApiError(409, "知识库尚未就绪"));
  expect(fetchMock.mock.calls[0][0]).toContain("taskType=rag");
});
