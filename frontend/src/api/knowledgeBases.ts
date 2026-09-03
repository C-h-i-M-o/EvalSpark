import { ApiError, fetchJson, readErrorMessage } from "./client";
import type { JobAccepted, KnowledgeBase, KnowledgeBaseForm, KnowledgeDocument, PageResult } from "../features/knowledge-bases/types";

const root = "/api/knowledge-bases";
export function listKnowledgeBases(page = 1, signal?: AbortSignal): Promise<PageResult<KnowledgeBase>> {
  return fetchJson(`${root}?page=${page}&pageSize=100`, signal);
}
export async function listAllKnowledgeBases(signal?: AbortSignal): Promise<KnowledgeBase[]> {
  const first = await listKnowledgeBases(1, signal);
  const result = [...first.items];
  for (let page = 2; page <= Math.ceil(first.total / first.pageSize); page += 1) {
    result.push(...(await listKnowledgeBases(page, signal)).items);
  }
  return result;
}
export function getKnowledgeBase(id: number, signal?: AbortSignal): Promise<KnowledgeBase> {
  return fetchJson(`${root}/${id}`, signal);
}
export function listDocuments(id: number, page = 1, signal?: AbortSignal): Promise<PageResult<KnowledgeDocument>> {
  return fetchJson(`${root}/${id}/documents?page=${page}&pageSize=20`, signal);
}
async function mutate<T>(path: string, method: "POST" | "PATCH" | "DELETE", payload?: KnowledgeBaseForm | FormData, signal?: AbortSignal): Promise<T> {
  const form = payload instanceof FormData;
  const response = await fetch(`${root}${path}`, { method, credentials: "include", signal,
    headers: { Accept: "application/json", ...(!form && payload ? { "Content-Type": "application/json" } : {}) },
    body: form ? payload : payload ? JSON.stringify(payload) : undefined });
  if (!response.ok) throw new ApiError(response.status, await readErrorMessage(response));
  return response.json() as Promise<T>;
}
export const createKnowledgeBase = (payload: KnowledgeBaseForm) => mutate<KnowledgeBase>("", "POST", payload);
export const updateKnowledgeBase = (id: number, payload: KnowledgeBaseForm) => mutate<KnowledgeBase>(`/${id}`, "PATCH", payload);
export const deleteKnowledgeBase = (id: number) => mutate<JobAccepted>(`/${id}`, "DELETE");
export const reindexKnowledgeBase = (id: number) => mutate<JobAccepted>(`/${id}/reindex`, "POST");
export const retryDocument = (id: number, documentId: number) => mutate<JobAccepted>(`/${id}/documents/${documentId}/retry`, "POST");
export const deleteDocument = (id: number, documentId: number) => mutate<JobAccepted>(`/${id}/documents/${documentId}`, "DELETE");
export function uploadDocument(id: number, file: File, signal?: AbortSignal): Promise<JobAccepted> {
  const form = new FormData();
  form.append("file", file);
  return mutate(`/${id}/documents`, "POST", form, signal);
}
export async function downloadDocument(id: number, document: KnowledgeDocument, signal?: AbortSignal): Promise<void> {
  const response = await fetch(`${root}/${id}/documents/${document.id}/download`, { credentials: "include", signal });
  if (!response.ok) throw new ApiError(response.status, await readErrorMessage(response));
  const url = URL.createObjectURL(await response.blob());
  const link = window.document.createElement("a");
  link.href = url;
  link.download = document.originalName;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}
