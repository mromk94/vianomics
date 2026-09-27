"use client";

import { X } from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";

type Toast = { id: number; message: string; tone: "info" | "pos" | "neg" };

const ToastCtx = createContext<(m: string, tone?: Toast["tone"]) => void>(
  () => {},
);

export function useToast() {
  return useContext(ToastCtx);
}

const TONE_CLS = {
  info: "border-border text-text",
  pos: "border-pos/40 text-pos",
  neg: "border-neg/40 text-neg",
};

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((message: string, tone: Toast["tone"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, message, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);

  const ctx = useMemo(() => push, [push]);

  return (
    <ToastCtx.Provider value={ctx}>
      {children}
      <div
        aria-live="polite"
        className="fixed right-4 bottom-4 z-[60] flex w-80 flex-col gap-2"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`flex items-start justify-between gap-2 rounded-md border bg-surface px-3 py-2 text-sm shadow-lg ${TONE_CLS[t.tone]}`}
          >
            <span>{t.message}</span>
            <button
              aria-label="Dismiss notification"
              onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))}
              className="text-faint hover:text-text"
            >
              <X className="size-3.5" />
            </button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
