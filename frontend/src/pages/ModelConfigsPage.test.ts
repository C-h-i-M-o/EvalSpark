import { createElement, Fragment } from "react";
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { expect, test, vi } from "vitest";

// 仅替换弹窗门户，让真实折叠面板与表单在无浏览器环境中渲染。
vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("antd")>();
  return {
    ...actual,
    Modal: ({ children }: { children?: ReactNode }) => createElement(Fragment, null, children)
  };
});

import { ModelConfigsPage } from "./ModelConfigsPage";

test("高级选项首次折叠时也挂载待校验和提交的字段", () => {
  const html = renderToStaticMarkup(createElement(MemoryRouter, null, createElement(ModelConfigsPage)));
  expect(html).toContain('aria-expanded="false"');
  for (const field of ["baseUrl", "temperature", "maxTokens", "timeoutSeconds", "priceInput", "priceOutput"]) {
    expect(html).toContain(`id="${field}"`);
  }
});

test.each(["/models?tab=embedding", "/embedding-config"])("%s 定位到合并后的 Embedding 页签", (path) => {
  const html = renderToStaticMarkup(createElement(MemoryRouter, { initialEntries: [path] }, createElement(ModelConfigsPage)));
  expect(html).toContain("知识库索引与 RAG 检索使用的向量模型接口");
  expect(html).not.toContain("新增配置");
});
