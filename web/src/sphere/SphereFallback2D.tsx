import { useEffect, useRef } from "react";
import type { MotionValue } from "motion/react";
import { useJarvisStore, type JarvisTheme } from "../store/useJarvisStore";
import type { SphereState } from "../types/generated";
import type { AudioEngine } from "./AudioEngine";
import { getStateVisualConfig } from "./stateVisuals";

interface SphereFallback2DProps {
  sphereState: SphereState;
  theme: JarvisTheme;
  amplitude: number;
  audioEngine?: AudioEngine;
  reducedMotion: boolean;
  pointerX?: MotionValue<number>;
  pointerY?: MotionValue<number>;
}

export function SphereFallback2D({
  sphereState,
  theme,
  amplitude,
  audioEngine,
  reducedMotion,
  pointerX,
  pointerY,
}: SphereFallback2DProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const propsRef = useRef({
    sphereState,
    theme,
    amplitude,
    audioEngine,
    reducedMotion,
    pointerX,
    pointerY,
  });
  propsRef.current = {
    sphereState,
    theme,
    amplitude,
    audioEngine,
    reducedMotion,
    pointerX,
    pointerY,
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let rafId = 0;
    let isVisible = true;
    const start = performance.now();
    let lastNow = start;

    const draw = (now: number) => {
      const {
        sphereState: curState,
        theme: curTheme,
        amplitude: propAmp,
        audioEngine: engine,
        reducedMotion: reduced,
        pointerX: ptrX,
        pointerY: ptrY,
      } = propsRef.current;
      const dtMs = Math.min(100, Math.max(0, now - lastNow));
      lastNow = now;
      const curAmplitude = engine
        ? engine.sample(dtMs, useJarvisStore.getState().micLevel).amplitude
        : propAmp;
      const elapsed = (now - start) / 1000;
      const cfg = getStateVisualConfig(curState, curTheme);
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = canvas.clientWidth || 320;
      const h = canvas.clientHeight || 320;
      if (canvas.width !== Math.floor(w * dpr) || canvas.height !== Math.floor(h * dpr)) {
        canvas.width = Math.floor(w * dpr);
        canvas.height = Math.floor(h * dpr);
      }

      ctx.save();
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, w, h);

      const motionMul = reduced || curState === "offline" ? 0 : 1;
      const px = motionMul ? (ptrX?.get() ?? 0) : 0;
      const py = motionMul ? (ptrY?.get() ?? 0) : 0;

      const isSpeaking = curState === "speaking";
      const speechVibe = isSpeaking
        ? (Math.sin(elapsed * 42.0) * 0.04 + (curAmplitude || 0.35) * 0.1) * motionMul
        : 0;

      const cx = w / 2 + px * 2.5;
      const cy = h / 2 - py * 2.5;
      const pulse =
        motionMul *
        (Math.sin(elapsed * cfg.noiseSpeed * 2.2) * 5 +
          curAmplitude * 22 +
          speechVibe * 60 +
          (isSpeaking ? Math.sin(elapsed * 28.0) * 7 : 0));
      const baseRadius = Math.min(w, h) * 0.34 + pulse;

      const grad = ctx.createRadialGradient(
        cx + px * baseRadius * 0.22,
        cy - py * baseRadius * 0.22,
        baseRadius * 0.12,
        cx,
        cy,
        baseRadius
      );
      grad.addColorStop(0, cfg.colorAccent);
      grad.addColorStop(0.45, cfg.colorB);
      grad.addColorStop(1, cfg.colorA);

      ctx.beginPath();
      ctx.arc(cx, cy, Math.max(24, baseRadius), 0, Math.PI * 2);
      ctx.fillStyle = grad;
      ctx.fill();

      ctx.strokeStyle = cfg.colorB;
      ctx.lineWidth = 1.4;
      const tilt = py * 0.25 + Math.sin(elapsed * cfg.coreSpeed) * 0.08 * motionMul;
      for (let i = -2; i <= 2; i += 1) {
        const yOffset = (i / 3) * baseRadius * 0.65;
        const rx = Math.sqrt(Math.max(0, baseRadius * baseRadius - yOffset * yOffset));
        ctx.beginPath();
        ctx.ellipse(
          cx,
          cy + yOffset,
          rx,
          Math.max(4, rx * (0.14 + Math.abs(tilt) * 0.18)),
          px * 0.18,
          0,
          Math.PI * 2
        );
        ctx.stroke();
      }

      const lonSpin = elapsed * cfg.ringSpeed * motionMul + px * 0.7;
      for (let j = 0; j < 2; j += 1) {
        const angle = lonSpin + (j * Math.PI) / 2;
        const rx = Math.max(4, Math.abs(Math.cos(angle)) * baseRadius);
        ctx.beginPath();
        ctx.ellipse(cx, cy, rx, baseRadius, py * 0.16, 0, Math.PI * 2);
        ctx.stroke();
      }

      ctx.restore();
      if (!document.hidden && isVisible && !reduced) {
        rafId = requestAnimationFrame(draw);
      }
    };

    const restartLoop = () => {
      cancelAnimationFrame(rafId);
      if (!document.hidden && isVisible && !propsRef.current.reducedMotion) {
        lastNow = performance.now();
        rafId = requestAnimationFrame(draw);
      }
    };

    rafId = requestAnimationFrame(draw);
    document.addEventListener("visibilitychange", restartLoop);
    let observer: IntersectionObserver | null = null;
    if (typeof IntersectionObserver !== "undefined") {
      observer = new IntersectionObserver(
        ([entry]) => {
          isVisible = entry ? entry.isIntersecting : true;
          restartLoop();
        },
        { threshold: 0.01 }
      );
      observer.observe(canvas);
    }

    return () => {
      cancelAnimationFrame(rafId);
      document.removeEventListener("visibilitychange", restartLoop);
      observer?.disconnect();
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      aria-label={`Sphere JARVIS en mode Canvas 2D (${sphereState})`}
      style={{ width: "100%", height: "100%", display: "block" }}
    />
  );
}
