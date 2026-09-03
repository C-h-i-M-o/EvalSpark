import type { RagEvidence } from "../features/rag/types";
import { formatRagNumber, invalidCitationLabels, sourceLabel } from "../features/rag/rag";

export function RagEvidencePanel({ evidence, rewrittenQuery, answer = "" }: { evidence: RagEvidence[]; rewrittenQuery?: string | null; answer?: string }) {
  return <section className="rag-evidence" aria-label="当前回答的检索证据">
    <h4>检索与引用来源</h4>
    <p><strong>模型改写：</strong>{rewrittenQuery || "尚无改写结果"}</p>
    <p className="rag-note">以下标签仅对应当前模型的回答。展开查看评测时保存的证据，不重新检索。</p>
    {evidence.map((item) => <details key={item.label} className="rag-evidence-item">
      <summary><strong>[{item.label}]</strong> {item.documentName}<small>{sourceLabel(item.source)} · 相似度 {formatRagNumber(item.similarity, 4)}</small></summary>
      <p className="rag-note">文档 #{item.documentId} · 版本 {item.indexRevision} · 块 {item.chunkId}</p>
      <pre className="rag-evidence-text">{item.text}</pre>
    </details>)}
    {!evidence.length && <p className="rag-note">暂无可展示的证据快照。</p>}
    {invalidCitationLabels(answer, evidence.map((item) => item.label)).map((label) =>
      <p key={label} className="rag-error" role="note">回答包含 [{label}]，但当前回答没有对应证据；不会跳转到其他模型的同名标签。</p>)}
  </section>;
}
