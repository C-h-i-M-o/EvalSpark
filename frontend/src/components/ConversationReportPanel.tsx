import { Button, InputNumber, Modal, Pagination, Select, Space, Tag } from "antd";
import type { ConversationRead } from "../api/conversationTypes";
import { useConversationReports } from "../features/conversation/useConversationReports";
import { ConversationAssessment } from "./ConversationAssessment";
import { conversationModelOptions, reportDetailRows, reportRows, unavailableReassess } from "../features/conversation/reportPresentation";

interface Props { conversation: ConversationRead | null; modelNames: Map<number, string>; disabled: boolean; onUsageChange?: () => Promise<void>; }

/** 展示固定分支会话报告及其原文评审详情。 */
export function ConversationReportPanel({ conversation, modelNames, disabled, onUsageChange }: Props) {
  const view = useConversationReports(conversation, disabled, onUsageChange);
  return <section className="conversation-report-panel" aria-label="会话报告">
    <Space wrap><strong>会话报告</strong><Tag>{view.owner ? "作者可提交" : "只读"}</Tag>
      {view.owner && <><Select aria-label="报告分支" value={view.modelConfigId ?? undefined} options={conversationModelOptions(view.modelIds, modelNames)} onChange={view.setModelConfigId} disabled={disabled || view.submitting} />
        <InputNumber<number> aria-label="报告截止轮次" min={1} max={view.maxTurn} precision={0} value={view.throughTurn || null} onChange={view.setThroughTurn} disabled={disabled || view.submitting} />
        <Button onClick={view.submit} loading={view.submitting} disabled={disabled || !view.judgeEnabled || view.modelConfigId === null || view.throughTurn < 1 || view.throughTurn > view.maxTurn}>生成报告</Button></>}
      <Button onClick={view.refresh} loading={view.loading}>刷新报告</Button>
    </Space>
    {!view.judgeEnabled && <p className="form-hint">本会话未配置独立评审模型，无法生成报告。</p>}
    {view.error && <p className="alert-message error">{view.error}</p>}
    <div aria-busy={view.loading}>{reportRows(view.reports, modelNames).map((row) => <article key={row.report.id}>
      <h4>{row.title}</h4>
      {row.successfulResponses !== null && <p>成功回答 {row.successfulResponses} 次</p>}
      {row.failedGenerationTurns.length > 0 && <p>生成失败轮次：{row.failedGenerationTurns.join("、")}</p>}
      {row.limitations.map((limitation, index) => <p className="form-hint" key={index}>评价限制：{limitation}</p>)}
      {row.report.result && <>
        <p>生成成功率 {row.statistics.generationRate} · 评分覆盖率 {row.statistics.scoreCoverage} · 原文来源覆盖率 {row.statistics.sourceCoverage}</p>
        <p className="form-hint">原文来源覆盖率仅表示可用原文已进入评审，不能证明跨轮关系已充分判断。</p>
        <details><summary>历史问题的后续状态</summary>
          <p className="form-hint">状态固定至本报告截止轮次；后续修正不会抹去历史失败评分，未知不代表问题仍存在。</p>
          {!row.resolutions.available ? <p>本报告未记录问题后续状态。</p> : <>
            {row.resolutions.items.length === 0 && <p>本报告未记录需要追踪的历史失败项。</p>}
            {row.resolutions.items.map((issue, index) => <article className="score-detail-item" key={index}>
              <strong>{issue.state} · 截至第 {issue.throughTurn ?? "未知"} 轮</strong>
              <p>{issue.description}</p><p>{issue.reason}</p>
              {issue.reviews.map((review, reviewIndex) => <section key={reviewIndex}>
                <h5>状态评审第 {review.index ?? "未知"} 组 · {review.state}</h5><p>{review.reason}</p>
                {review.references.map((reference, referenceIndex) => <blockquote key={referenceIndex}>
                  <p>第 {reference.turn} 轮 · {reference.sourceId}</p>{reference.quote}
                </blockquote>)}
              </section>)}
            </article>)}
          </>}
        </details>
        {row.statistics.reviews.length > 0 && <details><summary>各次评审的检查机会</summary>
          <p className="form-hint">每次完整评审独立统计；成功表示检查项达到 3/4 分且未明确失败，三次机会不累加。覆盖审核单列，不计作对话机会。</p>
          {row.statistics.reviews.map((review, index) => <section key={index}>
            <h5>第 {review.index ?? "未知"} 次评审</h5>
            {!review.valid ? <p>本次评审无效，无法统计检查机会。</p> : <div className="conversation-report-table"><table>
              <thead><tr><th scope="col">维度</th><th scope="col">机会</th><th scope="col">成功</th><th scope="col">未知</th><th scope="col">不适用</th><th scope="col">覆盖已审核</th><th scope="col">覆盖待判断</th><th scope="col">失败检查项</th></tr></thead>
              <tbody>{review.dimensions.map((dimension) => <tr key={dimension.key}><th scope="row">{dimension.label}</th><td>{dimension.opportunities}</td><td>{dimension.successful}</td><td>{dimension.unknown}</td><td>{dimension.notApplicable}</td><td>{dimension.coverageChecked}</td><td>{dimension.coverageUnknown}</td><td>{dimension.failed}</td></tr>)}</tbody>
            </table></div>}
            {review.findings.map((finding, index) => <article className="score-detail-item" key={index}>
              <strong>{finding.id} · {finding.label} · {finding.state}{finding.critical && " · 关键要求"}</strong>
              <p>{finding.reason}</p>
              {finding.references.map((reference, referenceIndex) => <blockquote key={referenceIndex}>
                <p>依据来自第 {reference.turn} 轮 · {reference.sourceId}</p>{reference.quote}
              </blockquote>)}
              {finding.references.length === 0 && <p className="form-hint">没有可定位轮次的结构化原文依据。</p>}
            </article>)}
          </section>)}
        </details>}
        {row.statistics.trend.length > 0 && <details><summary>RAG 证据评分趋势</summary>
          <p className="form-hint">以下为创建报告时冻结的逐轮快照；后续评分不会改写本报告。暂定分不参与正式排名。</p>
          <div className="conversation-report-table"><table><thead><tr><th scope="col">轮次</th><th scope="col">证据评分状态</th><th scope="col">证据分</th><th scope="col">覆盖率</th></tr></thead>
            <tbody>{row.statistics.trend.map((point, index) => <tr key={index}><th scope="row">{point.turn ?? "未知"}</th><td>{point.status}</td><td>{point.final}</td><td>{point.coverage}</td></tr>)}</tbody>
          </table></div>
        </details>}
      </>}
      <ConversationAssessment assessments={[row.report]} owner={false} disabled={view.loading} busy={[]} judgeEnabled={view.judgeEnabled} onDetail={view.showDetail} onReassess={unavailableReassess} />
    </article>)}</div>
    <Pagination current={view.page} total={view.total} pageSize={10} showSizeChanger={false} disabled={view.loading} onChange={view.changePage} />
    <Modal title="会话报告评审分段" open={view.detail !== null || view.detailLoading} footer={null} onCancel={view.closeDetail} width={760}>
      {view.detailLoading && <p>正在读取评审依据……</p>}
      {reportDetailRows(view.detail).map((run) => <section key={run.run}>
        <h4>评审序号 {run.reviewIndex}{run.batchIndex !== null && <> · 分段 {run.batchIndex}</>}</h4>
        {run.findings.map((finding, index) => <article className="score-detail-item" key={index}><strong>{finding.id} · {finding.label}</strong><p>{finding.reason}</p>
          {finding.references.length > 0 ? finding.references.map((reference, referenceIndex) => <blockquote key={referenceIndex}><p>依据来自第 {reference.turn} 轮 · {reference.sourceId}</p>{reference.quote}</blockquote>)
            : finding.evidence.map((quote, quoteIndex) => <blockquote key={quoteIndex}>{quote}</blockquote>)}
        </article>)}
      </section>)}
    </Modal>
  </section>;
}
