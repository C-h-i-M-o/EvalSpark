import { Alert, Button, Form, Input, InputNumber, Pagination, Popconfirm, Skeleton, Space, Tag } from "antd";
import { Link } from "react-router-dom";
import { useKnowledgeBases } from "../features/knowledge-bases/useKnowledgeBases";
import { formatBytes, jobProgress, knowledgeStatusLabels } from "../features/knowledge-bases/knowledgeBases";
import { RAG_DELETE_NOTICE } from "../features/rag/rag";
import type { KnowledgeBaseForm } from "../features/knowledge-bases/types";

export function KnowledgeBasesPage() {
  const view = useKnowledgeBases();
  return <section className="rag-page">
    <header className="page-head"><div><h2>私有知识库</h2><p>管理检索资料、切分配置与索引进度。只有你能访问这些内容。</p></div>
      <Space wrap><Button onClick={view.refresh} disabled={view.busy}>刷新</Button><Button type="primary" onClick={view.beginCreate} disabled={view.busy}>新建知识库</Button></Space></header>
    {view.error && <Alert type="error" showIcon title={view.error} />}
    {view.notice && <Alert type="info" showIcon title={view.notice} />}
    {view.formMode && <section className="rag-surface">
      <h3>{view.formMode === "create" ? "新建知识库" : "编辑知识库"}</h3>
      <Form<KnowledgeBaseForm> form={view.form} layout="vertical" onFinish={view.save} disabled={view.busy}>
        <Form.Item name="name" label="知识库名称" rules={[{ required: true, whitespace: true, message: "请输入名称" }, { max: 120, message: "最多 120 字" }]}><Input maxLength={120} /></Form.Item>
        <Form.Item name="description" label="说明"><Input.TextArea maxLength={2000} rows={2} /></Form.Item>
        <div className="rag-form-pair">
          <Form.Item name="chunkSize" label="切块大小（Token）" rules={[{ required: true }]}><InputNumber min={128} max={2048} precision={0} /></Form.Item>
          <Form.Item name="chunkOverlap" label="重叠大小（Token）" rules={[{ required: true }]}><InputNumber min={0} max={2047} precision={0} /></Form.Item>
        </div>
        <p className="rag-note">使用 Qwen 分词器切分。重叠必须小于切块大小；修改参数会使整个库需要重建，重建完成前不可用于新评测。</p>
        <Space><Button type="primary" htmlType="submit" loading={view.busy}>保存配置</Button><Button onClick={view.closeForm}>取消</Button></Space>
      </Form>
    </section>}
    <div className="knowledge-layout">
      <aside className="rag-surface knowledge-list" aria-label="我的知识库">
        <h3>我的资料 · {view.libraries.total}</h3>
        {view.loading && !view.libraryRows.length ? <Skeleton active paragraph={{ rows: 3 }} /> : null}
        {view.libraryRows.map((library) => <button key={library.id} type="button" disabled={view.busy} onClick={library.select}
          aria-pressed={view.selectedId === library.id} className={view.selectedId === library.id ? "knowledge-item active" : "knowledge-item"}>
          <strong>{library.name}</strong><span>{knowledgeStatusLabels[library.status]} · {library.documentCount}/100 份</span>
        </button>)}
        {!view.loading && !view.libraryRows.length && <p className="rag-note">先新建知识库，再上传一份文档开始索引。</p>}
        <Pagination size="small" current={view.libraryPage} total={view.libraries.total} pageSize={100} showSizeChanger={false} onChange={view.setLibraryPage} hideOnSinglePage />
      </aside>
      <section className="rag-surface knowledge-workspace" aria-busy={view.loading}>
        {!view.selected ? <p className="rag-note">{view.loading ? "正在读取知识库……" : "请选择一个知识库。"}</p> : <>
          <header className="rag-toolbar"><div><h3>{view.selected.name}</h3><Tag color={view.selected.available ? "success" : "default"}>{knowledgeStatusLabels[view.selected.status]}</Tag></div>
            <Space wrap><Button onClick={view.beginEdit} disabled={!view.canModify}>编辑配置</Button>
              <Popconfirm title="重建整个知识库？" description="完成前将暂停新评测，历史结果不受影响。" onConfirm={view.reindex} okText="重建" cancelText="取消"><Button disabled={!view.canModify || view.selected.status === "indexing"}>重建索引</Button></Popconfirm>
              <Popconfirm title="删除整个知识库？" description={RAG_DELETE_NOTICE} onConfirm={view.removeLibrary} okText="确认删除" cancelText="取消"><Button danger disabled={view.busy || view.selected.status === "deleted"}>删除知识库</Button></Popconfirm>
            </Space></header>
          <p>{view.selected.description || "未填写说明"}</p>
          <p className="rag-note">版本 {view.selected.contentRevision} · {view.selected.documentCount}/100 份文档 · {view.selected.chunkCount.toLocaleString("zh-CN")}/100,000 块 · 切块 {view.selected.chunkSize} / 重叠 {view.selected.chunkOverlap} Token</p>
          {view.selected.errorCode && <Alert type="warning" title={`处理状态：${view.selected.errorCode}`} />}
          <div className="rag-upload"><label htmlFor="knowledge-upload">上传资料（每次一份，最大 20 MB）</label>
            <input id="knowledge-upload" type="file" accept=".txt,.md,.pdf,.docx" disabled={!view.canModify || view.selected.documentCount >= 100} onChange={view.upload} />
            <p className="rag-note">支持 TXT、Markdown、文本型 PDF、DOCX；扫描 PDF 不做 OCR。索引完成后可<Link to="/rag">进入 RAG 评测</Link>。</p></div>
          <div className="rag-table-scroll"><table className="rag-table"><caption>文档与索引状态（每 5 秒更新）</caption>
            <thead><tr><th>文档</th><th>处理状态</th><th>块数</th><th>操作</th></tr></thead>
            <tbody>{view.documentRows.map((document) => <tr key={document.id}>
              <td><strong>{document.originalName}</strong><small>{formatBytes(document.sizeBytes)} · 版本 {document.indexRevision}</small></td>
              <td>{knowledgeStatusLabels[document.status]}<small>{jobProgress(document.currentJob)}</small>
                {(document.errorCode || document.currentJob?.errorCode) && <small className="rag-error">{document.errorCode || document.currentJob?.errorCode}</small>}
                {document.currentJob?.dispatchPending && <small>等待队列恢复投递</small>}</td>
              <td>{document.chunkCount}</td><td><Space wrap>
                <Button size="small" onClick={document.download} disabled={!view.canModify || ["deleting", "deleted"].includes(document.status)}>下载</Button>
                {document.status === "failed" && <Button size="small" onClick={document.retry} disabled={!view.canModify}>重试</Button>}
                <Popconfirm title="删除文档？" description={RAG_DELETE_NOTICE} onConfirm={document.remove} okText="删除" cancelText="取消"><Button size="small" danger disabled={view.busy || document.status === "deleted" || view.selected?.status === "deleted"}>删除</Button></Popconfirm>
              </Space></td></tr>)}</tbody></table></div>
          {!view.documentRows.length && <p className="rag-note">尚无文档。上传资料后，系统会依次解析、切分、向量化并发布索引。</p>}
          <Pagination current={view.documentPage} total={view.documents.total} pageSize={20} showSizeChanger={false} onChange={view.setDocumentPage} hideOnSinglePage />
        </>}
      </section>
    </div>
  </section>;
}
