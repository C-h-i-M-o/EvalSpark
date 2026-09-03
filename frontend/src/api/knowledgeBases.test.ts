import { afterEach, expect, test, vi } from "vitest";
import { createKnowledgeBase, uploadDocument, listKnowledgeBases } from "./knowledgeBases";
afterEach(() => vi.unstubAllGlobals());
test("知识库请求复用 Cookie，上传不强写 multipart 边界且保留取消信号", async () => {
  const fetchMock = vi.fn().mockImplementation(async () => new Response("{}", { headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
  await createKnowledgeBase({ name: "测试", description: "", chunkSize: 800, chunkOverlap: 120 });
  expect(fetchMock.mock.calls[0][1]).toMatchObject({ credentials: "include", method: "POST" });
  const signal = new AbortController().signal;
  await uploadDocument(1, new File(["正文"], "资料.txt"), signal);
  const upload = fetchMock.mock.calls[1][1] as RequestInit;
  expect(upload.body).toBeInstanceOf(FormData);
  expect(upload.signal).toBe(signal);
  expect(upload.headers).not.toHaveProperty("Content-Type");
  await listKnowledgeBases(2, signal);
  expect(fetchMock.mock.calls[2][0]).toContain("page=2");
});
