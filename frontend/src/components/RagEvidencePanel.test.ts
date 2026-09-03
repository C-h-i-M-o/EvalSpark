import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "vitest";
import { RagEvidencePanel } from "./RagEvidencePanel";
import { CommentPanel } from "./CommentPanel";
import type { RagEvidence } from "../features/rag/types";

test("相同 S1 只渲染本回答快照，文档文本不能变为 HTML", () => {
  const evidence: RagEvidence = { label: "S1", documentId: 1, documentName: "当前模型.txt", chunkId: "a", indexRevision: 1,
    text: "<script>窃取资料</script>", similarity: 0.8, source: { kind: "text", lineStart: 2, lineEnd: 5 } };
  const html = renderToStaticMarkup(createElement(RagEvidencePanel, { evidence: [evidence], answer: "回答 [S1] [S9]", rewrittenQuery: "查询" }));
  expect(html).toContain("当前模型.txt");
  expect(html).toContain("&lt;script&gt;");
  expect(html).not.toContain("<script>");
  expect(html).toContain("[S9]");
  expect(html).toContain("<summary>");
  const other = renderToStaticMarkup(createElement(RagEvidencePanel, { evidence: [{ ...evidence, text: "另一模型的证据" }] }));
  expect(other).not.toContain("窃取资料");
});
test("私有 RAG 评论不沿用公开提示", () => {
  const html = renderToStaticMarkup(createElement(CommentPanel, { responseId: 1, privateDiscussion: true }));
  expect(html).toContain("仅自己可见");
  expect(html).not.toContain("评论公开展示");
});
