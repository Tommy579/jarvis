import { useSyncExternalStore } from "react";

export type SchemePreference = "system" | "light" | "dark";
export type ResolvedScheme = "light" | "dark";

const STORAGE_KEY = "jarvis_color_scheme";
const listeners = new Set<() => void>();

function readStored(): SchemePreference {
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

function systemScheme(): ResolvedScheme {
  if (typeof window === "undefined" || !window.matchMedia) return "dark";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyMeta(pref: SchemePreference) {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="color-scheme"]');
  if (meta) meta.content = pref === "system" ? "light dark" : pref;
}

function emit() {
  for (const l of listeners) l();
}

let snapshot = "";
function getSnapshot(): string {
  const pref = readStored();
  const next = `${pref}|${pref === "system" ? systemScheme() : pref}|${systemScheme()}`;
  if (next !== snapshot) snapshot = next;
  return snapshot;
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
  const onStorage = (e: StorageEvent) => {
    if (e.key === STORAGE_KEY) {
      applyMeta(readStored());
      cb();
    }
  };
  mq?.addEventListener?.("change", cb);
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(cb);
    mq?.removeEventListener?.("change", cb);
    window.removeEventListener("storage", onStorage);
  };
}

export function setSchemePreference(pref: SchemePreference) {
  try {
    if (pref === "system") window.localStorage.removeItem(STORAGE_KEY);
    else window.localStorage.setItem(STORAGE_KEY, pref);
  } catch {
    // Stockage indisponible : on applique quand meme pour la session.
  }
  applyMeta(pref);
  emit();
}

export function useColorScheme(): {
  preference: SchemePreference;
  resolved: ResolvedScheme;
  system: ResolvedScheme;
} {
  const snap = useSyncExternalStore(subscribe, getSnapshot, () => "system|dark|dark");
  const [preference, resolved, system] = snap.split("|") as [
    SchemePreference,
    ResolvedScheme,
    ResolvedScheme,
  ];
  return { preference, resolved, system };
}
