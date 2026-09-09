import { useCallback, useEffect, useRef, useState } from "react";
import { getTodayTokenUsage } from "../../api/client";
import type { TokenUsage } from "../../api/client";

export function useTodayTokenUsage(running: boolean) {
  const [tokenUsage, setTokenUsage] = useState<TokenUsage | null>(null);
  const [tokenUsageLoading, setLoading] = useState(false);
  const [tokenUsageErrorMessage, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  const mounted = useRef(false);
  const loadTokenUsage = useCallback(async (): Promise<void> => {
    if (!mounted.current) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    try {
      const usage = await getTodayTokenUsage(controller.signal);
      if (!controller.signal.aborted) { setTokenUsage(usage); setError(""); }
    } catch (error) {
      if (!controller.signal.aborted) setError(error instanceof Error ? error.message : "今日 Token 用量加载失败");
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    const refresh = () => { void loadTokenUsage(); };
    const visible = () => { if (document.visibilityState === "visible") refresh(); };
    refresh();
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", visible);
    return () => {
      mounted.current = false;
      request.current?.abort();
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [loadTokenUsage]);

  useEffect(() => {
    if (!running) return;
    // 两种评测读取同一账本，逐模型入账后及时显示；终态另立即刷新。
    const timer = window.setInterval(() => { void loadTokenUsage(); }, 5000);
    return () => window.clearInterval(timer);
  }, [running, loadTokenUsage]);

  return { tokenUsage, tokenUsageLoading, tokenUsageErrorMessage, loadTokenUsage };
}
