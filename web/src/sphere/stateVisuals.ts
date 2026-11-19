import type { JarvisTheme } from "../store/useJarvisStore";
import type { SphereState } from "../types/generated";

export interface StateVisualConfig {
  stateIndex: number;
  colorA: string;
  colorB: string;
  colorAccent: string;
  noiseScale: number;
  noiseSpeed: number;
  ringSpeed: number;
  coreSpeed: number;
  label: string;
}

export function getStateVisualConfig(state: SphereState, theme: JarvisTheme): StateVisualConfig {
  const isBlue = theme === "jarvis-blue";
  const quietCore = isBlue ? "#0c2233" : "#2e1a05";
  const accentSoft = isBlue ? "#38bdf8" : "#d97706";
  const accentBright = isBlue ? "#7dd3fc" : "#fbbf24";

  switch (state) {
    case "listening":
      return {
        stateIndex: 1,
        colorA: quietCore,
        colorB: accentBright,
        colorAccent: isBlue ? "#bae6fd" : "#fde68a",
        noiseScale: 1.85,
        noiseSpeed: 0.75,
        ringSpeed: 0.45,
        coreSpeed: 0.28,
        label: "Ecoute",
      };
    case "thinking":
      return {
        stateIndex: 2,
        colorA: isBlue ? "#0f2d4a" : "#3b2106",
        colorB: isBlue ? "#38bdf8" : "#f59e0b",
        colorAccent: isBlue ? "#7dd3fc" : "#fbbf24",
        noiseScale: 2.65,
        noiseSpeed: 1.35,
        ringSpeed: 1.25,
        coreSpeed: 0.65,
        label: "Reflexion",
      };
    case "speaking":
      return {
        stateIndex: 3,
        colorA: quietCore,
        colorB: isBlue ? "#0ea5e9" : "#f59e0b",
        colorAccent: accentBright,
        noiseScale: 2.15,
        noiseSpeed: 1.1,
        ringSpeed: 0.7,
        coreSpeed: 0.38,
        label: "Parole",
      };
    case "alert":
      return {
        stateIndex: 4,
        colorA: "#3b0d0d",
        colorB: "#dc2626",
        colorAccent: "#f87171",
        noiseScale: 2.35,
        noiseSpeed: 1.25,
        ringSpeed: 0.95,
        coreSpeed: 0.5,
        label: "Alerte",
      };
    case "offline":
      return {
        stateIndex: 5,
        colorA: "#2b3442",
        colorB: "#64748b",
        colorAccent: "#cbd5e1",
        noiseScale: 0.9,
        noiseSpeed: 0.0,
        ringSpeed: 0.0,
        coreSpeed: 0.0,
        label: "Hors ligne",
      };
    case "idle":
    default:
      return {
        stateIndex: 0,
        colorA: quietCore,
        colorB: accentSoft,
        colorAccent: accentBright,
        noiseScale: 1.4,
        noiseSpeed: 0.45,
        ringSpeed: 0.22,
        coreSpeed: 0.16,
        label: "Veille",
      };
  }
}
