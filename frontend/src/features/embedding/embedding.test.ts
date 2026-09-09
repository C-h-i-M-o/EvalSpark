import { afterEach, expect, it, vi } from "vitest";
import { sendEmbeddingConfig } from "./useEmbeddingConfig";
import type { EmbeddingForm } from "./useEmbeddingConfig";
import { resolveRouteAccess } from "../navigation/navigation";

afterEach(() => vi.unstubAllGlobals());

it("普通用户不能访问 Embedding 管理页", () => {
  expect(resolveRouteAccess("/embedding-config", { id: 1, username: "测试", role: "user", status: "active" }))
    .toEqual({ type: "redirect", to: "/" });
});

it("保存版本与空密钥按原值传输，连接测试使用独立路径", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ version: 3 }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  const signal = new AbortController().signal;
  const payload: EmbeddingForm = { version: 2, baseUrl: "http://localhost:8080/v1", apiKey: "", modelName: "embedding",
    dimensions: 3, queryPrefix: "", timeoutSeconds: 60, batchSize: 16, maxInputCharacters: 2048, enabled: true };
  await sendEmbeddingConfig(payload, false, signal);
  expect(fetchMock).toHaveBeenCalledWith("/api/admin/embedding-config", expect.objectContaining({
    method: "PUT", signal, credentials: "include", body: JSON.stringify(payload)
  }));
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ success: true }), { status: 200 }));
  await sendEmbeddingConfig(payload, true, signal);
  expect(fetchMock).toHaveBeenLastCalledWith("/api/admin/embedding-config/test", expect.objectContaining({ method: "POST" }));
});
