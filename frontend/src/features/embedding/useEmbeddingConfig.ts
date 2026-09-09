import { useEffect, useRef, useState } from "react";
import { Form, Modal } from "antd";
import { ApiError, fetchJson, readErrorMessage } from "../../api/client";
import { errorMessage } from "../rag/rag";

export interface EmbeddingConfig {
  version: number;
  configured: boolean;
  baseUrl: string;
  hasApiKey: boolean;
  modelName: string;
  dimensions: number;
  queryPrefix: string;
  timeoutSeconds: number;
  batchSize: number;
  maxInputCharacters: number;
  enabled: boolean;
}
export type EmbeddingForm = Omit<EmbeddingConfig, "configured" | "hasApiKey"> & { apiKey?: string; clearApiKey?: boolean };

export async function sendEmbeddingConfig<T>(payload: EmbeddingForm, test: boolean, signal: AbortSignal): Promise<T> {
  const response = await fetch(`/api/admin/embedding-config${test ? "/test" : ""}`, {
    method: test ? "POST" : "PUT", credentials: "include", signal,
    headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(payload)
  });
  if (!response.ok) throw new ApiError(response.status, await readErrorMessage(response));
  return response.json() as Promise<T>;
}

export function useEmbeddingConfig() {
  const [form] = Form.useForm<EmbeddingForm>();
  const [config, setConfig] = useState<EmbeddingConfig | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const controller = useRef<AbortController | null>(null);
  const pending = useRef(false);
  function apply(value: EmbeddingConfig) {
    setConfig(value);
    const { configured: _configured, hasApiKey: _hasApiKey, ...fields } = value;
    form.setFieldsValue({ ...fields, apiKey: "", clearApiKey: false });
  }
  useEffect(() => {
    const abort = new AbortController(); controller.current = abort;
    void fetchJson<EmbeddingConfig>("/api/admin/embedding-config", abort.signal)
      .then((value) => { if (!abort.signal.aborted) apply(value); })
      .catch((caught: unknown) => { if (!abort.signal.aborted) setError(errorMessage(caught)); });
    return () => { controller.current?.abort(); };
  }, []);
  async function execute(test: boolean) {
    if (pending.current || !config) return;
    let fields: EmbeddingForm;
    try { fields = await form.validateFields(); } catch { return; }
    pending.current = true; setBusy(true); setError(""); setNotice("");
    const abort = new AbortController(); controller.current = abort;
    try {
      const payload = { ...fields, version: config.version };
      if (test) {
        const result = await sendEmbeddingConfig<{ message: string }>(payload, true, abort.signal);
        if (!abort.signal.aborted) setNotice(result.message);
      } else {
        const value = await sendEmbeddingConfig<EmbeddingConfig>(payload, false, abort.signal);
        if (!abort.signal.aborted) { apply(value); setNotice("配置已保存。索引相关参数变更后，请在知识库中重建索引。"); }
      }
    } catch (caught) {
      if (!abort.signal.aborted) setError(errorMessage(caught));
    } finally {
      pending.current = false;
      if (!abort.signal.aborted) setBusy(false);
    }
  }
  function save() {
    Modal.confirm({ title: "保存全局 Embedding 配置", content: "所有知识库统一使用此配置。地址、模型、维度或查询前缀变化会要求重建已有索引；运行中的任务会阻止保存。",
      okText: "保存", cancelText: "取消", onOk: () => execute(false) });
  }
  function test() { void execute(true); }
  return { form, config, busy, error, notice, save, test };
}
