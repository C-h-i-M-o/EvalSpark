import { Select } from "antd";
import { ModelResponseCard } from "../components/ModelResponseCard";
import { useHistory } from "../features/history/useHistory";
import { formatHistoryTime, historyStatusClass, historyStatusText, historyEmptyCopy } from "../features/history/history";

export function HistoryPage({ taskType }: { taskType: "chat" | "rag" }) {
  const view = useHistory(taskType);
  return <section className="history-page">
    <header className="page-head"><div><p className="eyebrow">评测记录</p><h2>{taskType === "rag" ? "RAG 评测历史" : "普通评测历史"}</h2></div>
      <button type="button" onClick={view.refresh} disabled={view.loading}>{view.loading ? "刷新中" : "刷新"}</button></header>
    {view.error && <p className="alert-message error">{view.error}</p>}
    <section className="history-layout">
      <aside className="history-panel">
        <div className="history-list-head"><div><p className="panel-label">任务列表</p><h3>最近评测</h3></div><span>{view.listing.total} 条</span></div>
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
            <p>{view.selectedTask.ownerUsername} · {view.selectedTask.taskType === "rag" ? "RAG 评测" : "普通评测"}</p></div>
            {view.canChangeVisibility && <Select aria-label="任务可见性" loading={view.visibilitySaving} disabled={view.visibilitySaving || view.detailLoading} value={view.selectedTask.visibility} onChange={view.changeVisibility} options={[{ value: "private", label: "私有" }, { value: "public", label: "公开" }]} />}
            {view.selectedStatus && <span className={"status-badge " + historyStatusClass(view.selectedStatus)}>{historyStatusText(view.selectedStatus)}</span>}</div>
            {view.selectedTask.responses.length === 0 ? <div className="history-empty-detail"><h4>{historyEmptyCopy(view.selectedTask).title}</h4><p>{historyEmptyCopy(view.selectedTask).description}</p></div> :
              <div className="history-response-list">{view.selectedTask.responses.map((response) => <ModelResponseCard key={response.id} response={response} elapsedSeconds={0} feedbackSubmitting={view.feedbackIds.includes(response.id)} showComments privateDiscussion={view.selectedTask?.visibility === "private"} onFeedback={view.submitFeedback} />)}</div>}
          </div>}
      </section>
    </section>
  </section>;
}
