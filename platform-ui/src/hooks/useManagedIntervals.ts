import { useCallback, useEffect, useRef } from "react";

/** 注册页面轮询并在组件卸载时统一清理，避免路由切换后继续请求。 */
export function useManagedIntervals() {
  const intervals = useRef<Set<number>>(new Set());

  const clearManagedInterval = useCallback((interval: number) => {
    window.clearInterval(interval);
    intervals.current.delete(interval);
  }, []);

  const setManagedInterval = useCallback((callback: () => void, delayMs: number) => {
    const interval = window.setInterval(callback, delayMs);
    intervals.current.add(interval);
    return interval;
  }, []);

  useEffect(() => () => {
    intervals.current.forEach(interval => window.clearInterval(interval));
    intervals.current.clear();
  }, []);

  return { setManagedInterval, clearManagedInterval };
}
