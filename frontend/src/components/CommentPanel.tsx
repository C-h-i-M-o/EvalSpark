import { useEffect, useState } from "react";

import {
  ApiError,
  createResponseComment,
  deleteResponseComment,
  listResponseComments
} from "../api/client";
import type { CommentRead } from "../features/evaluation/types";
import { formatHistoryTime } from "../features/history/history";

interface CommentPanelProps {
  responseId: number;
  privateDiscussion?: boolean;
}

const pageSize = 10;

export function CommentPanel({ responseId, privateDiscussion = false }: CommentPanelProps) {
  const [comments, setComments] = useState<CommentRead[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  useEffect(() => {
    void loadComments(1);
  }, [responseId]);

  async function loadComments(nextPage: number): Promise<void> {
    setLoading(true);
    setErrorMessage("");
    try {
      const result = await listResponseComments(responseId, { page: nextPage, pageSize });
      setComments(result.items);
      setTotal(result.total);
      setPage(result.page);
    } catch (error) {
      setErrorMessage(getErrorMessage(error, "评论加载失败"));
    } finally {
      setLoading(false);
    }
  }

  async function submitComment(): Promise<void> {
    const trimmedContent = content.trim();
    if (!trimmedContent || submitting) {
      return;
    }
    setSubmitting(true);
    setErrorMessage("");
    try {
      await createResponseComment(responseId, trimmedContent);
      setContent("");
      await loadComments(1);
    } catch (error) {
      setErrorMessage(getErrorMessage(error, "评论发布失败"));
    } finally {
      setSubmitting(false);
    }
  }

  async function removeComment(commentId: number): Promise<void> {
    setErrorMessage("");
    try {
      await deleteResponseComment(commentId);
      const nextTotal = Math.max(total - 1, 0);
      const lastPage = Math.max(Math.ceil(nextTotal / pageSize), 1);
      await loadComments(Math.min(page, lastPage));
    } catch (error) {
      setErrorMessage(getErrorMessage(error, "评论删除失败"));
    }
  }

  const totalPages = Math.max(Math.ceil(total / pageSize), 1);

  return (
    <section className="comment-panel">
      <header className="comment-panel-head">
        <div>
          <p className="panel-label">{privateDiscussion ? "私有讨论" : "公开讨论"}</p>
          <h4>回答评论</h4>
        </div>
        <span>{total} 条</span>
      </header>
      <textarea
        value={content}
        maxLength={1000}
        rows={3}
        placeholder="写下你对这个回答的看法"
        onChange={(event) => setContent(event.target.value)}
      />
      <div className="comment-actions">
        <span>{privateDiscussion ? "评论仅自己可见，不参与评分。" : "评论公开展示，不参与评分。"}</span>
        <button type="button" disabled={!content.trim() || submitting} onClick={() => void submitComment()}>
          {submitting ? "发布中" : "发布评论"}
        </button>
      </div>
      {errorMessage ? <p className="alert-message error">{errorMessage}</p> : null}
      <div className="comment-list" aria-busy={loading}>
        {comments.map((comment) => (
          <article key={comment.id} className="comment-item">
            <header>
              <div>
                <strong>{comment.username}</strong>
                <time>{formatHistoryTime(comment.createdAt)}</time>
              </div>
              {comment.canDelete ? (
                <button type="button" onClick={() => void removeComment(comment.id)}>
                  删除
                </button>
              ) : null}
            </header>
            <p>{comment.content}</p>
          </article>
        ))}
        {!loading && comments.length === 0 ? <p className="empty-note">还没有评论，来写第一条吧。</p> : null}
      </div>
      {totalPages > 1 ? (
        <nav className="pagination">
          <button type="button" disabled={page <= 1} onClick={() => void loadComments(page - 1)}>
            上一页
          </button>
          <span>
            {page} / {totalPages}
          </span>
          <button type="button" disabled={page >= totalPages} onClick={() => void loadComments(page + 1)}>
            下一页
          </button>
        </nav>
      ) : null}
    </section>
  );
}

function getErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError || error instanceof Error) {
    return error.message || fallback;
  }
  return fallback;
}
