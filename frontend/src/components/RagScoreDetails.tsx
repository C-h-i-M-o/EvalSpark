import type { EvaluationScore } from "../features/evaluation/types";
import type { RagDetail } from "../features/rag/types";
import { formatRagNumber } from "../features/rag/rag";
import { ragDimensionLabels, usageStageLabels, failureStageLabels } from "../features/rag/scorePresentation";

export function RagScoreSummary({ rag }: { rag: RagDetail }) {
  return <dl className="rag-dimensions">{ragDimensionLabels.map((dimension) => <div key={dimension.key}>
    <dt>{dimension.label}</dt><dd>{formatRagNumber(rag.judgeAggregate?.[dimension.key])}</dd>
  </div>)}</dl>;
}

export function RagScoreDetails({ rag, score }: { rag: RagDetail; score: EvaluationScore }) {
  return <section className="rag-score-details" aria-label="RAG 评分与用量">
    <h4>RAG 联合评审</h4>
    <p className="rag-note">知识库：{rag.knowledgeBaseName} · 内容版本 {rag.contentRevision} · 切块 {rag.chunkSize} / 重叠 {rag.chunkOverlap} Token</p>
    <details><summary>Embedding 模型版本</summary><code>{rag.embeddingRevision}</code></details>
    <p>{score.judgeComment || "评审尚未完成"} · 有效 {rag.judgeAggregate?.validRunCount ?? 0}/3 轮</p>
    {rag.failureStage && <p className="rag-error">失败阶段：{failureStageLabels[rag.failureStage]} · {rag.errorCode}</p>}
    <dl className="rag-dimensions">{ragDimensionLabels.map((dimension) => <div key={dimension.key}><dt>{dimension.label}</dt>
      <dd>{formatRagNumber(rag.judgeAggregate?.[dimension.key])} / 10</dd><small>极差 {formatRagNumber(rag.judgeAggregate?.ranges[dimension.rangeKey])}</small></div>)}</dl>
    <p className="rag-note">至少两轮完整有效，且四项极差都不超过 2 才计分。忠实度衡量资料支持程度，不代表外部事实正确性。</p>
    <p>RAG 分 {formatRagNumber(rag.ragFinal)} · 基础分 {formatRagNumber(rag.baseFinal)} · 最终分 {formatRagNumber(score.final)}</p>
    <p className="rag-formula">RAG = 忠实度 × 50% + 引用正确性 × 30% + 引用完整性 × 20%<br />基础分 = 规则 × 20% + 回答质量 × 30% + RAG × 50%<br />有反馈：最终分 = 基础分 × 90% + 反馈分 × 10%；无反馈则保持基础分。仅最终显示分舍入。</p>
    {rag.judgeRuns.map((run) => <details key={run.runIndex} className="rag-judge-run"><summary>第 {run.runIndex} 轮 · {run.errorCode ? "无效" : "有效"}</summary>
      {run.errorCode && <p className="rag-error">{run.errorCode}</p>}
      {run.result && <><dl className="rag-dimensions">{ragDimensionLabels.map((dimension) => <div key={dimension.key}><dt>{dimension.label}</dt><dd>{formatRagNumber(run.result?.[dimension.key])}</dd></div>)}</dl>
        <ol className="rag-claims">{run.result.claims.map((claim, index) => <li key={index}>
          <strong>{claim.claim}</strong><p>{claim.reason}</p><small>资料支持：{claim.supported ? "是" : "否"} · 需要引用：{claim.needsCitation ? "是" : "否"} · 实际引用支持：{claim.citationSupported ? "是" : "否"}</small>
          <p>证据：{claim.evidenceLabels.join("、") || "无"}{claim.invalidCitationLabels.length ? `；无效引用：${claim.invalidCitationLabels.join("、")}` : ""}</p>
        </li>)}</ol></>}
      {run.rawResult && <details><summary>结构化原始结果</summary><pre className="rag-evidence-text">{JSON.stringify(run.rawResult, null, 2)}</pre></details>}
    </details>)}
    <h4>全链路用量{rag.hasUnknownUsage ? "（已知小计）" : ""}</h4>
    <p>外部 Token {formatRagNumber(rag.externalTotalTokens, 0)} · {Object.entries(rag.costByCurrency).map(([currency, cost]) => `${formatRagNumber(cost, 6)} ${currency}`).join(" / ") || "暂无已知外部费用"}</p>
    {rag.hasUnknownUsage && <p className="rag-error">部分调用用量未知，下方不是精确总量；未知不等于零。</p>}
    <div className="rag-table-scroll"><table className="rag-table"><caption>改写、Embedding、回答与三轮 Judge</caption>
      <thead><tr><th>阶段/模型</th><th>输入/输出/缓存命中/缓存创建</th><th>总 Token</th><th>耗时</th><th>费用</th></tr></thead>
      <tbody>{rag.stageUsage.map((usage) => <tr key={`${usage.stage}-${usage.runIndex}`}>
        <td>{usageStageLabels[usage.stage]}{usage.stage === "judge" ? ` ${usage.runIndex}` : ""}<small>{usage.model?.displayName ?? "私有 Qwen Embedding"}</small>
          {usage.model && <details><summary>调用配置</summary><pre className="rag-evidence-text">{JSON.stringify(usage.model, null, 2)}</pre></details>}</td>
        <td>{formatRagNumber(usage.inputTokens, 0)} / {formatRagNumber(usage.outputTokens, 0)} / {formatRagNumber(usage.cacheHitTokens, 0)} / {formatRagNumber(usage.cacheCreationTokens, 0)}</td>
        <td>{usage.status === "known" ? formatRagNumber(usage.totalTokens, 0) : usage.status === "pending" ? "待返回" : "未知"}</td>
        <td>{formatRagNumber(usage.latencyMs, 0)} ms</td>
        <td>{usage.stage === "embed" ? "本地，不计外部费用" : `${formatRagNumber(usage.estimatedCost, 6)} ${usage.model?.currency ?? ""}`}</td>
      </tr>)}</tbody></table></div>
    <p className="rag-note">卡片顶部耗时、输出与成本仅代表回答生成阶段。不同币种分别汇总，Embedding 不计入外部额度。</p>
  </section>;
}
