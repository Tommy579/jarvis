import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppErrorBoundary } from "../components/AppErrorBoundary";
import { installGlobalErrorHandlers, resetGlobalErrorDedupe } from "../lib/globalErrors";
import { useToastStore } from "../store/useToastStore";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function Boom(): never {
  throw new Error("boum de rendu");
}

describe("AppErrorBoundary", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    vi.spyOn(console, "error").mockImplementation(() => undefined);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  it("affiche un ecran de secours quand un enfant leve pendant le rendu", () => {
    act(() => {
      root.render(
        <AppErrorBoundary>
          <Boom />
        </AppErrorBoundary>
      );
    });

    expect(container.textContent).toContain("L'interface s'est interrompue");
    expect(container.textContent).toContain("boum de rendu");
    const buttons = Array.from(container.querySelectorAll("button")).map((b) => b.textContent);
    expect(buttons).toContain("Recharger l'interface");
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
  });

  it("rend les enfants normalement quand tout va bien", () => {
    act(() => {
      root.render(
        <AppErrorBoundary>
          <p>contenu normal</p>
        </AppErrorBoundary>
      );
    });
    expect(container.textContent).toBe("contenu normal");
  });
});

describe("installGlobalErrorHandlers", () => {
  beforeEach(() => {
    resetGlobalErrorDedupe();
    useToastStore.setState({ toasts: [] });
    vi.spyOn(console, "error").mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("signale une erreur globale sans spammer le meme message", () => {
    const uninstall = installGlobalErrorHandlers(window);
    try {
      act(() => {
        window.dispatchEvent(new ErrorEvent("error", { message: "panne reseau" }));
        window.dispatchEvent(new ErrorEvent("error", { message: "panne reseau" }));
      });
      const toasts = useToastStore.getState().toasts;
      expect(toasts).toHaveLength(1);
      expect(toasts[0]?.message).toContain("panne reseau");
      expect(toasts[0]?.tone).toBe("error");
    } finally {
      uninstall();
    }
  });

  it("signale un rejet de promesse non gere", () => {
    const uninstall = installGlobalErrorHandlers(window);
    try {
      act(() => {
        const event = new Event("unhandledrejection") as Event & { reason?: unknown };
        event.reason = new Error("delai depasse");
        window.dispatchEvent(event);
      });
      expect(useToastStore.getState().toasts.at(-1)?.message).toContain("delai depasse");
    } finally {
      uninstall();
    }
  });
});