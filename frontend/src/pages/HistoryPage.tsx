import { Segmented } from "antd";
import { ModelResponseCard } from "../components/ModelResponseCard";
import { useHistory } from "../features/history/useHistory";
import { formatHistoryTime, historyStatusClass, historyStatusText, historyEmptyCopy } from "../features/history/history";

export function HistoryPage() {
  const view = useHistory();
  return <section className="history-page">
    <header className="page-head"><div><p className="eyebrow">History</p><h2>历史任务</h2></div>
      <button type="button" onClick={view.refresh} disabled={view.loading}>{view.loading ? "刷新中" : "刷新"}</button></header>
    {view.error && <p className="alert-message error">{view.error}</p>}
    <section className="history-layout">
      <aside className="history-panel">
        <div className="history-list-head"><div><p className="panel-label">任务列表</p><h3>最近评测</h3></div><span>{view.listing.total} 条</span></div>
        <Segmented<"all" | "chat" | "rag"> value={view.taskType} onChange={view.changeType} options={[{ value: "all", label: "全部" }, { value: "chat", label: "普通评测" }, { value: "rag", label: "RAG 评测" }]} aria-label="任务类型" />
        <div className="history-list" aria-busy={view.loading}>{view.rows.map((item) =>
          <button key={item.taskId} type="button" className={view.selectedTask?.taskId === item.taskId ? "history-item active" : "history-item"} onClick={item.select}>
            <span className="history-item-title">{item.prompt}</span>
            <span className="history-item-meta">{item.ownerUsername} · {formatHistoryTime(item.createdAt)} · {item.responseCount} 个回答</span>
            <span className="history-item-tags"><i>{item.taskType === "rag" ? "RAG" : "普通评测"}</i><i>{item.visibility === "private" ? "私有" : "公开"}</i><i className={historyStatusClass(item)}>{historyStatusText(item)}</i></span>
          </button>)}{!view.loading && !view.rows.length && <p className="empty-note">暂无历史任务。</p>}</div>
        <div className="pagination"><select aria-label="每页任务数" value={view.pageSize} onChange={view.changePageSize}>{[10, 20, 50].map((size) => <option key={size} value={size}>{size} / 页</option>)}</select>
          <button type="button" disabled={view.page <= 1 || view.loading} onClick={view.previousPage}>上一页</button><span>{view.page} / {view.totalPages}</span>
          <button type="button" disabled={view.page >= view.totalPages || view.loading} onClick={view.nextPage}>下一页</button></div>
      </aside>
      <section className="history-detail" aria-busy={view.detailLoading}>
        {!view.selectedTask ? <p className="empty-note">{view.detailLoading ? "正在读取任务详情……" : "请选择一个历史任务。"}</p> :
          <div className="history-detail-body"><div className="history-detail-head"><div><p className="panel-label">任务详情</p><h3>{view.selectedTask.prompt}</h3>
            <p>{view.selectedTask.ownerUsername} · {view.selectedTask.taskType === "rag" ? "私有 RAG 评测" : view.selectedTask.visibility === "private" ? "私有评测" : "公开评测"}</p></div>
            {view.selectedStatus && <span className={"status-badge " + historyStatusClass(view.selectedStatus)}>{historyStatusText(view.selectedStatus)}</span>}</div>
            {view.selectedTask.responses.length === 0 ? <div className="history-empty-detail"><h4>{historyEmptyCopy(view.selectedTask).title}</h4><p>{historyEmptyCopy(view.selectedTask).description}</p></div> :
              <div className="history-response-list">{view.selectedTask.responses.map((response) => <ModelResponseCard key={response.id} response={response} elapsedSeconds={0} feedbackSubmitting={view.feedbackIds.includes(response.id)} showComments onFeedback={view.submitFeedback} />)}</div>}
          </div>}
      </section>
    </section>
  </section>;
}
