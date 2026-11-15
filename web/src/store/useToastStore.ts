import { create } from "zustand";

export type ToastTone = "ok" | "error" | "info";

export interface Toast {
  id: number;
  message: string;
  tone: ToastTone;
}

interface ToastState {
  toasts: Toast[];
  push: (message: string, tone?: ToastTone) => void;
  dismiss: (id: number) => void;
}

let seq = 0;

export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],
  push: (message, tone = "ok") => {
    const id = ++seq;
    set((s) => ({ toasts: [...s.toasts.slice(-3), { id, message, tone }] }));
    window.setTimeout(() => get().dismiss(id), tone === "error" ? 7000 : 4000);
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

export const toast = {
  ok: (m: string) => useToastStore.getState().push(m, "ok"),
  error: (m: string) => useToastStore.getState().push(m, "error"),
  info: (m: string) => useToastStore.getState().push(m, "info"),
};

export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
