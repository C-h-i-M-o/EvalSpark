import { expect, test } from "vitest";
import { validateUpload } from "./knowledgeBases";
test("上传格式和十进制 20 MB 上限与后端一致", () => {
  expect(validateUpload(new File(["正文"], "资料.md"))).toBeNull();
  expect(validateUpload(new File([], "空.txt"))).toContain("空文件");
  expect(validateUpload(new File(["正文"], "脚本.html"))).toContain("仅支持");
  const file = new File(["正文"], "大文件.pdf");
  Object.defineProperty(file, "size", { value: 20_000_001 });
  expect(validateUpload(file)).toContain("20 MB");
});
