const KNOWN_MODEL_LABELS: Record<string, string> = {
  "gemini-4-hydrogen": "Gemini 4 Hydrogen",
  "gemini-4-argon": "Gemini 4 Argon",
  "gemini-4-carbon": "Gemini 4 Carbon",
  "gemini-3-flash-preview": "Gemini 3 Flash",
  "gemini-3.1-pro-preview": "Gemini 3.1 Pro",
  "gemini-3.5-flash-lite": "Gemini 3.5 Flash Lite",
  "gemini-live": "Gemini Live",
  "deepseek/deepseek-v4.1-flash-1017": "DeepSeek V4.1 Flash",
  "z-ai/glm-5.3": "GLM 5.3",
  "minimax/minimax-m3": "MiniMax M3",
  "minimax-m3:cloud": "MiniMax M3 (Cloud)",
  "moonshotai/kimi-k3": "Kimi K3",
  "qwen3.5:2b": "Qwen 3.5 2B (local)",
  "qwen3.5:0.8b": "Qwen 3.5 0.8B (local)",
  "gemma4:31b-cloud": "Gemma 4 31B",
  "nemotron-3-ultra:cloud": "Nemotron 3 Ultra",
};

export function formatModelLabel(modelId: string): string {
  const trimmed = (modelId || "").trim();
  if (!trimmed) return "";
  const direct = KNOWN_MODEL_LABELS[trimmed];
  if (direct) return direct;
  const withoutProvider = trimmed.includes("/") ? trimmed.split("/").pop() ?? trimmed : trimmed;
  if (KNOWN_MODEL_LABELS[withoutProvider]) return KNOWN_MODEL_LABELS[withoutProvider];
  return withoutProvider
    .replace(/-\d{4,}$/, "")
    .replace(/[-_]+/g, " ")
    .replace(/\b([a-z])/g, (m) => m.toUpperCase());
}
