import type { DocumentStatus, KnowledgeBaseForm, KnowledgeBaseStatus, RagJob } from "./types";

export const DEFAULT_KNOWLEDGE_FORM: KnowledgeBaseForm = { name: "", description: "", chunkSize: 800, chunkOverlap: 120 };
export const knowledgeStatusLabels: Record<KnowledgeBaseStatus | DocumentStatus, string> = {
  empty: "空知识库", queued: "排队中", parsing: "解析中", embedding: "向量化中", indexing: "索引中",
  ready: "已就绪", reindex_required: "需要重建", failed: "处理失败", deleting: "删除中", deleted: "已删除"
};
export function validateUpload(file: File): string | null {
  if (!/\.(txt|md|pdf|docx)$/i.test(file.name)) return "仅支持 TXT、Markdown、PDF 和 DOCX 文件";
  if (file.size === 0) return "不能上传空文件";
  if (file.size > 20_000_000) return "每个文档最多 20 MB（20,000,000 字节）";
  return null;
}
export function formatBytes(bytes: number): string { return `${(bytes / 1_000_000).toLocaleString("zh-CN", { maximumFractionDigits: 2 })} MB`; }
export function jobProgress(job: RagJob | null): string {
  if (!job) return "";
  const stageNames: Record<string, string> = { queued: "等待处理", parsing: "解析", chunking: "切分", embedding: "向量化",
    indexing: "写入索引", publishing: "发布", deleting: "清理", completed: "完成", failed: "失败" };
  return `${stageNames[job.stage] ?? "处理"} ${job.processedCount}/${job.totalCount || "待确定"} · 尝试 ${job.attempt}`;
}
