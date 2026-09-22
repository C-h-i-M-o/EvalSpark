import { Alert, Button, Input, InputNumber, Modal, Pagination, Space, Tag } from "antd";
import type { ConversationVisibility } from "../api/conversationTypes";
import type { AvailableModelConfig } from "../features/evaluation/types";
import { useConversationWorkspace } from "../features/conversation/useConversationWorkspace";
import { assessmentFindings } from "../features/conversation/assessment";
import { contextStatusLabels } from "../features/conversation/contextStatus";
import { CONVERSATION_PAGE_SIZE } from "../features/conversation/workspaceData";
import { ModelResponseCard } from "./ModelResponseCard";
import { ConversationAssessment } from "./ConversationAssessment";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { ConversationReportPanel } from "./ConversationReportPanel";

interface ConversationPanelProps {
  mode?: "chat" | "rag";
  knowledgeBaseId?: number | null;
  availableModels: AvailableModelConfig[];
  selectedModelIds: number[];
  judgeModelId: number | null;
  enableThinking: boolean;
  visibility: ConversationVisibility;
  disabled?: boolean;
  createDisabled?: boolean;
  onUsageChange?: () => Promise<void>;
}

/** 渲染普通多轮问答、分页与独立新评分，业务操作集中在 Hook。 */
export function ConversationPanel(props: ConversationPanelProps) {
  const view = useConversationWorkspace({ ...props, disabled: props.disabled ?? false });
  return <section className="query-panel conversation-panel">
    <div className="query-header">
      <div><p className="panel-label">{props.mode === "rag" ? "RAG 多轮" : "普通多轮"}</p><h3>{view.state.conversation?.title ?? "连续追问工作台"}</h3></div>
      <Space wrap>
        {view.state.conversation && <Tag>已进行 {view.state.conversation.currentTurn} 轮</Tag>}
        {view.state.conversation && <Button disabled={view.running || view.loading} onClick={view.refresh}>刷新会话</Button>}
        {view.state.conversation && <Button disabled={view.running} onClick={view.leaveConversation}>返回新建</Button>}
      </Space>
    </div>
    <p className="form-hint">同一问题并行发送，各模型独立保留历史。创建后固定候选模型、评审模型与思考设置。</p>
    {view.state.errorMessage && <Alert type="error" showIcon title={view.state.errorMessage} />}
    {!view.state.conversation && <>
      <Space wrap><label htmlFor="conversation-input-budget">输入预算 Token</label>
        <InputNumber<number> id="conversation-input-budget" min={1024} max={view.budget.maximum} precision={0}
          value={view.inputBudget || null} onChange={view.changeInputBudget} disabled={view.creating || view.loading || !view.budget.canCreate} />
        <Button type="primary" loading={view.creating || view.loading}
          disabled={props.createDisabled || view.disabled || view.loading || !view.validBudget || props.selectedModelIds.length === 0 || (props.mode === "rag" && !props.knowledgeBaseId)} onClick={() => void view.startConversation()}>新建多轮会话</Button>
      </Space><p className="form-hint">{view.budget.message}</p>
    </>}
    {view.state.conversation && <>
      <Tag>冻结输入预算 {view.frozenInputBudget} Token</Tag>
      <Space wrap>{view.frozenModelIds.map((id) => <Tag key={id}>{view.modelNames.get(id) ?? `模型 ${id}`}</Tag>)}<Tag>{view.state.conversation.visibility === "private" ? "私有会话" : "公开会话"}</Tag></Space>
      {!view.state.conversation.canContinue && <Alert type="info" title="公开会话只读，只有作者可以继续提问或发起复评" />}
      {view.state.conversation.generationStatus !== "idle" && !view.running && <Alert type="info" title="服务端仍在生成，请稍后刷新会话" />}
      <div className="conversation-turn-list" aria-busy={view.loading}>
        {view.state.turns.map((turn) => <article className="conversation-turn" key={turn.turn}>
          <h4>第 {turn.turn} 轮</h4><p className="conversation-question">{turn.prompt}</p>
          {contextStatusLabels(turn.contexts, view.modelNames).map((label) => <p className="form-hint" key={label}>{label}</p>)}
          {turn.responses.length > 0 ? <div className="response-grid">
            {turn.responses.map((response) => <ModelResponseCard key={response.id} response={response} elapsedSeconds={0}
              feedbackSubmitting={view.isFeedbackBusy(response.id)} onFeedback={view.submitFeedback}
              branchBusy={view.running || view.loading}
              branchAction={typeof turn.turnId === "number" && turn.turn === view.state.conversation?.currentTurn && turn.status !== "running"
                && response.answer !== "用户已明确跳过本轮回答" && typeof response.modelConfigId === "number"
                ? (action) => void view.branchAction(turn, response.modelConfigId as number, action) : undefined}
              assessmentContent={<ConversationAssessment assessments={typeof response.id === "number" ? view.assessments[response.id] ?? [] : []}
                owner={view.state.conversation?.canContinue ?? false} disabled={view.running || view.loading}
                busy={view.reassessmentBusy} judgeEnabled={typeof view.state.conversation?.configuration.judgeModelId === "number"}
                onDetail={view.showAssessment} onReassess={view.reassess} />} />)}
          </div> : <div className="response-grid">
            {view.frozenModelIds.map((id) => <article className="response-card" key={id}>
              <strong>{view.modelNames.get(id) ?? `模型 ${id}`}</strong>
              {turn.answers[id] ? <MarkdownRenderer content={turn.answers[id]} /> : <p>{turn.status === "running" ? "生成中……" : "暂无回答"}</p>}
            </article>)}
          </div>}
        </article>)}
      </div>
      <Pagination current={view.page} total={view.total} pageSize={CONVERSATION_PAGE_SIZE} showSizeChanger={false}
        disabled={view.running || view.loading} onChange={view.changePage} />
      <ConversationReportPanel key={view.state.conversation.id} conversation={view.state.conversation} modelNames={view.modelNames} disabled={view.running || view.loading} onUsageChange={props.onUsageChange} />
      <Input.TextArea value={view.prompt} disabled={!view.canSend} rows={3} placeholder="输入下一轮问题；Enter 发送，Shift+Enter 换行"
        onChange={view.changePrompt} onKeyDown={view.handlePromptKeyDown} />
      <Button type="primary" loading={view.running} disabled={!view.canSend || !view.prompt.trim()} onClick={() => void view.submitTurn()}>继续追问</Button>
    </>}
    <Modal title="多轮评审依据" open={view.detail !== null} footer={null} onCancel={view.closeAssessment} width={760}>
      {view.detail && assessmentFindings(view.detail).map((run) => <section key={run.run}>
        <h4>第 {run.run} 次评审 · {run.status}</h4>
        {run.findings.map((finding, index) => <article className="score-detail-item" key={index}>
          <strong>{finding.id} · {finding.label}</strong><p>{finding.reason}</p>
          {finding.references.length > 0 ? finding.references.map((reference, referenceIndex) => <blockquote key={referenceIndex}><p>依据来自第 {reference.turn} 轮 · {reference.sourceId}</p>{reference.quote}</blockquote>)
            : finding.evidence.map((quote, quoteIndex) => <blockquote key={quoteIndex}>{quote}</blockquote>)}
        </article>)}
      </section>)}
    </Modal>
  </section>;
}
