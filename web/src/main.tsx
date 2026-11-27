import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { AppErrorBoundary } from "./components/AppErrorBoundary";
import { AuthGate } from "./components/AuthGate";
import { installGlobalErrorHandlers } from "./lib/globalErrors";
import "./styles/theme.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 10_000,
      refetchOnWindowFocus: false,
    },
  },
});

if (import.meta.env.PROD && typeof navigator !== "undefined" && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  });
}

installGlobalErrorHandlers();

const rootEl = document.getElementById("root");
if (rootEl) {
  createRoot(rootEl).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <AuthGate>
          <AppErrorBoundary>
            <App />
          </AppErrorBoundary>
        </AuthGate>
      </QueryClientProvider>
    </StrictMode>
  );
}
