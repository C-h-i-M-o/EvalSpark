import { useEffect, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import { Form } from "antd";
import * as api from "../../api/knowledgeBases";
import { ApiError, fetchJson } from "../../api/client";
import { errorMessage } from "../rag/rag";
import { DEFAULT_KNOWLEDGE_FORM, validateUpload } from "./knowledgeBases";
import type { KnowledgeBase, KnowledgeBaseForm, KnowledgeDocument, PageResult } from "./types";

export function useKnowledgeBases() {
  const [chunkUnit, setChunkUnit] = useState("计数单位加载中");
  useEffect(() => {
    const controller = new AbortController();
    void fetchJson<{ chunkUnit: "characters" | "tokens" }>("/api/embedding-config", controller.signal)
      .then((value) => { if (!controller.signal.aborted) setChunkUnit(value.chunkUnit === "characters" ? "字符" : "Token"); })
      .catch(() => { if (!controller.signal.aborted) setChunkUnit("单位暂不可用"); });
    return () => controller.abort();
  }, []);
  const [form] = Form.useForm<KnowledgeBaseForm>();
  const [libraries, setLibraries] = useState<PageResult<KnowledgeBase>>({ items: [], total: 0, page: 1, pageSize: 100 });
  const [libraryPage, setLibraryPage] = useState(1);
  const [documents, setDocuments] = useState<PageResult<KnowledgeDocument>>({ items: [], total: 0, page: 1, pageSize: 20 });
  const [documentPage, setDocumentPage] = useState(1);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selected, setSelected] = useState<KnowledgeBase | null>(null);
  const [formMode, setFormMode] = useState<"create" | "edit" | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [revision, setRevision] = useState(0);
  const mounted = useRef(true);
  const mutation = useRef<AbortController | null>(null);
  const readAbort = useRef<AbortController | null>(null);
  const busyRef = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; mutation.current?.abort(); }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    readAbort.current = controller;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setLoading(true);
    async function load() {
      try {
        const [list, library, files] = await Promise.all([
          api.listKnowledgeBases(libraryPage, controller.signal),
          selectedId === null ? null : api.getKnowledgeBase(selectedId, controller.signal),
          selectedId === null ? null : api.listDocuments(selectedId, documentPage, controller.signal)
        ]);
        if (controller.signal.aborted) return;
        setLibraries(list);
        setSelected(library);
        if (files) setDocuments(files);
        if (selectedId === null && list.items[0]) setSelectedId(list.items[0].id);
        setError("");
      } catch (caught) {
        if (!controller.signal.aborted) {
          setError(errorMessage(caught));
          if (caught instanceof ApiError && caught.status === 404) { setSelectedId(null); setSelected(null); }
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          // 页面退出、切换库/分页时清理定时器和未完成读取，禁止旧结果覆盖。
          timer = setTimeout(() => { void load(); }, 5000);
        }
      }
    }
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [libraryPage, selectedId, documentPage, revision]);
  function refresh() { setRevision((value) => value + 1); }
  function selectLibrary(id: number) {
    if (busyRef.current) return;
    readAbort.current?.abort();
    setSelectedId(id); setSelected(null); setDocumentPage(1); setDocuments({ items: [], total: 0, page: 1, pageSize: 20 });
    setFormMode(null); setError(""); setNotice("");
    refresh();
  }
  async function act(action: (signal: AbortSignal) => Promise<unknown>, message: string) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setError(""); setNotice("");
    const controller = new AbortController(); mutation.current = controller;
    try {
      await action(controller.signal);
      if (mounted.current) { setNotice(message); refresh(); }
    } catch (caught) {
      if (mounted.current && !controller.signal.aborted) setError(errorMessage(caught));
    } finally {
      busyRef.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  function beginCreate() { form.setFieldsValue({ ...DEFAULT_KNOWLEDGE_FORM }); setFormMode("create"); }
  function beginEdit() {
    if (!selected) return;
    form.setFieldsValue({ name: selected.name, description: selected.description ?? "", chunkSize: selected.chunkSize, chunkOverlap: selected.chunkOverlap });
    setFormMode("edit");
  }
  function closeForm() { setFormMode(null); }
  async function save(values: KnowledgeBaseForm) {
    if (values.chunkOverlap >= values.chunkSize) { setError("重叠 Token 必须小于切块 Token"); return; }
    await act(async () => {
      const result = formMode === "edit" && selectedId !== null
        ? await api.updateKnowledgeBase(selectedId, values) : await api.createKnowledgeBase(values);
      if (mounted.current) { setSelectedId(result.id); setSelected(result); setFormMode(null); }
    }, "知识库配置已保存。修改切分参数后请重建索引。");
  }
  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0]; event.currentTarget.value = "";
    if (!file || selectedId === null) return;
    const issue = validateUpload(file);
    if (issue) { setError(issue); return; }
    await act((signal) => api.uploadDocument(selectedId, file, signal), "上传已受理，正在等待异步索引；以文档状态为准。");
  }
  async function reindex() {
    if (selectedId !== null) await act(() => api.reindexKnowledgeBase(selectedId), "重建已受理，完成前不能发起新的 RAG 评测。");
  }
  async function removeLibrary() {
    if (selectedId !== null) await act(async () => {
      await api.deleteKnowledgeBase(selectedId);
      if (mounted.current) { setSelectedId(null); setSelected(null); setFormMode(null); }
    }, "删除已受理，后台仍需清理原文件、文本和向量。");
  }
  const documentRows = documents.items.map((document) => ({ ...document,
    download: () => act((signal) => api.downloadDocument(document.knowledgeBaseId, document, signal), "原文件下载已开始。"),
    retry: () => act(() => api.retryDocument(document.knowledgeBaseId, document.id), "重试已受理，请等待状态更新。"),
    remove: () => act(() => api.deleteDocument(document.knowledgeBaseId, document.id), "删除已受理，历史评测中的证据快照仍保留。")
  }));
  return { chunkUnit, form, formMode, beginCreate, beginEdit, closeForm, save, libraries, libraryPage, setLibraryPage,
    documentRows, documents, documentPage, setDocumentPage, selected, selectedId,
    libraryRows: libraries.items.map((library) => ({ ...library, select: () => selectLibrary(library.id) })),
    loading, busy, error, notice, refresh, upload, reindex, removeLibrary,
    canModify: selected !== null && !["deleting", "deleted"].includes(selected.status) && !busy };
}
