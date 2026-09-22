import { Alert, Button, Input, Select, Skeleton, Space, Switch } from "antd";
import { Link } from "react-router-dom";
import { ModelResponseCard } from "../components/ModelResponseCard";
import { ConversationPanel } from "../components/ConversationPanel";
import { useRagEvaluation } from "../features/rag/useRagEvaluation";
import { formatRagNumber } from "../features/rag/rag";

export function RagEvaluationPage() {
  const view = useRagEvaluation();
  return <section className="rag-page">
    <header className="page-head"><div><p className="eyebrow">知识增强评测</p><h2>RAG 评测</h2><p>比较模型如何检索资料、引用证据与忠实作答。</p></div></header>
    <section className="token-usage-panel">
      <div><p className="panel-label">今日 Token</p><strong>{view.tokenUsage?.unlimited ? "管理员账号不限额" : "普通评测与 RAG 共用每日额度"}</strong></div>
      <dl>
        <div><dt>已使用</dt><dd>{formatRagNumber(view.tokenUsage?.usedTokens, 0)}</dd></div>
        <div><dt>剩余</dt><dd>{view.tokenUsage?.unlimited ? "不限额" : formatRagNumber(view.tokenUsage?.remainingTokens, 0)}</dd></div>
        <div><dt>每日额度</dt><dd>{view.tokenUsage?.unlimited ? "不限额" : formatRagNumber(view.tokenUsage?.dailyLimit, 0)}</dd></div>
      </dl>
    </section>
    <section className="rag-surface">
      <div className="rag-toolbar"><h3>选择资料与模型</h3><Space wrap><Link to="/knowledge-bases">管理知识库</Link><Button onClick={view.refresh} disabled={view.running || view.initialLoading}>刷新配置</Button></Space></div>
      {view.initialLoading && !view.models.length ? <Skeleton paragraph={{ rows: 2 }} /> : null}
      <div className="rag-form-pair">
        <div className="rag-field"><label htmlFor="rag-library">私有知识库</label><Select id="rag-library" value={view.libraryId} options={view.libraryOptions} onChange={view.selectLibrary} disabled={view.running} placeholder="选择已就绪的知识库" /></div>
        <div className="rag-field"><label htmlFor="rag-judge">独立评审模型 · 固定三轮</label><Select id="rag-judge" value={view.judgeId} options={view.idleOptions} onChange={view.setJudgeId} disabled={view.running} placeholder="保留一个未参与回答的模型" /></div>
      </div>
      {!view.initialLoading && !view.libraries.some((base) => base.available) && <p className="rag-note">没有可用知识库，请先上传文档并等待索引完成。</p>}
      {view.library && <p className="rag-note">当前资料版本 {view.library.contentRevision} · {view.library.documentCount} 份文档 · {view.library.chunkCount} 块</p>}
      <fieldset className="rag-models"><legend>候选回答模型</legend><Space wrap>{view.modelOptions.map((model) =>
        <Button key={model.id} type={model.selected ? "primary" : "default"} aria-pressed={model.selected} disabled={view.running} onClick={model.toggle}>{model.displayName}</Button>)}</Space></fieldset>
      {!view.initialLoading && !view.idleOptions.length && <Alert type="warning" title="请至少保留一个空闲模型用于联合评审；仅有一个可用模型时无法开始。" />}
      <div className="rag-field"><label htmlFor="rag-prompt">原始问题</label><Input.TextArea id="rag-prompt" rows={5} value={view.prompt} disabled={view.running} onChange={view.changePrompt} onKeyDown={view.handleKeyDown} placeholder="输入问题；Enter 开始，Shift + Enter 换行" /></div>
      <div className="rag-field"><label htmlFor="rag-visibility">评测可见性</label><Select id="rag-visibility" value={view.visibility} disabled={view.running} onChange={view.setVisibility} options={[{ value: "private", label: "私有评测" }, { value: "public", label: "公开评测" }]} /></div>
      <div className="rag-toolbar"><Space><Switch aria-label="思考模式" checked={view.enableThinking} onChange={view.setEnableThinking} disabled={view.running} />思考模式</Space>
        <Space><Button type="primary" disabled={!view.canSubmit} onClick={view.submit}>开始评测</Button>{view.running && <Button danger onClick={view.cancel}>停止</Button>}</Space></div>
      <p className="rag-note">回答与评审完成后计入今日额度，并发或长任务可能超出剩余值。</p>
    </section>
    <ConversationPanel mode="rag" knowledgeBaseId={view.libraryId} availableModels={view.models}
      selectedModelIds={view.selectedModelIds} judgeModelId={view.judgeId} enableThinking={view.enableThinking}
      visibility={view.visibility} disabled={view.running} createDisabled={!view.library?.available}
      onUsageChange={view.loadTokenUsage} />
    {view.quotaExhausted && <Alert type="error" title="今日 Token 额度已用完，请明日再试或联系管理员调整额度。" />}
    {view.error && <Alert type="error" showIcon title={view.error} />}
    {view.notice && <Alert type="info" showIcon title={view.notice} />}
    {view.running && <p className="rag-running" role="status">正在执行检索、回答与三轮评审 · 已等待 {view.elapsed} 秒</p>}
    {view.task?.taskId && <p className="rag-note">任务 #{view.task.taskId} · <Link to="/rag/history">在历史任务查看持久化结果</Link></p>}
    {!!view.task?.responses.length && <section className="response-grid">{view.task.responses.map((response) =>
      <ModelResponseCard key={response.modelConfigId ?? response.id} response={response} elapsedSeconds={view.elapsed}
        feedbackSubmitting={typeof response.id === "number" && view.feedbackIds.includes(response.id)} onFeedback={view.feedback} />)}</section>}
  </section>;
}
