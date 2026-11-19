import { Component, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { motion, type MotionValue, useMotionValue, useSpring, useTransform } from "motion/react";
import * as THREE from "three";
import { useJarvisStore } from "../store/useJarvisStore";
import { AudioEngine } from "./AudioEngine";
import { SphereFallback2D } from "./SphereFallback2D";
import { pointFragmentShader, pointVertexShader, sphereFragmentShader, sphereVertexShader } from "./shaders";
import { getStateVisualConfig } from "./stateVisuals";

function detectWebGL(): boolean {
  if (typeof window === "undefined") return false;
  if (new URLSearchParams(window.location.search).get("fallback2d") === "1") {
    return false;
  }
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") || canvas.getContext("webgl"));
  } catch {
    return false;
  }
}

class CanvasErrorBoundary extends Component<
  { fallback: ReactNode; children: ReactNode },
  { hasError: boolean }
> {
  state = { hasError: false };
  static getDerivedStateFromError() {
    return { hasError: true };
  }
  render() {
    return this.state.hasError ? this.props.fallback : this.props.children;
  }
}

interface CoreMeshProps {
  audioEngine: AudioEngine;
  reducedMotion: boolean;
  pointerX: MotionValue<number>;
  pointerY: MotionValue<number>;
}

function SphereCoreMesh({ audioEngine, reducedMotion, pointerX, pointerY }: CoreMeshProps) {
  const sphereState = useJarvisStore((s) => s.sphereState);
  const theme = useJarvisStore((s) => s.theme);
  const micLevel = useJarvisStore((s) => s.micLevel);
  const setAudioMetrics = useJarvisStore((s) => s.setAudioMetrics);

  const parallaxGroupRef = useRef<THREE.Group | null>(null);
  const coreMeshRef = useRef<THREE.Mesh | null>(null);
  const wireMeshRef = useRef<THREE.Mesh | null>(null);
  const pointsRef = useRef<THREE.Points | null>(null);
  const ringOuterRef = useRef<THREE.Mesh | null>(null);
  const ringInnerRef = useRef<THREE.Mesh | null>(null);

  const wireMatRef = useRef<THREE.MeshBasicMaterial | null>(null);
  const ringOuterMatRef = useRef<THREE.MeshBasicMaterial | null>(null);
  const ringInnerMatRef = useRef<THREE.MeshBasicMaterial | null>(null);

  const colorA = useMemo(() => new THREE.Color("#0c2233"), []);
  const colorB = useMemo(() => new THREE.Color("#38bdf8"), []);
  const colorAccent = useMemo(() => new THREE.Color("#7dd3fc"), []);
  const targetA = useMemo(() => new THREE.Color(), []);
  const targetB = useMemo(() => new THREE.Color(), []);
  const targetAccent = useMemo(() => new THREE.Color(), []);

  const uniforms = useMemo(
    () => ({
      uTime: { value: 0 },
      uAmplitude: { value: 0 },
      uLow: { value: 0 },
      uMid: { value: 0 },
      uHigh: { value: 0 },
      uState: { value: 0 },
      uColorA: { value: colorA },
      uColorB: { value: colorB },
      uColorAccent: { value: colorAccent },
      uNoiseScale: { value: 1.4 },
      uNoiseSpeed: { value: 0.45 },
      uPointer: { value: new THREE.Vector2(0, 0) },
    }),
    [colorA, colorB, colorAccent]
  );

  const pointUniforms = useMemo(
    () => ({
      uColor: { value: new THREE.Color("#bae6fd") },
      uOpacity: { value: 0.34 },
      uPointSize: { value: 2.6 },
      uPixelRatio: { value: typeof window !== "undefined" ? Math.min(window.devicePixelRatio || 1, 2) : 1 },
    }),
    []
  );

  useFrame((_, delta) => {
    const dt = Math.min(0.1, delta);
    const dtMs = dt * 1000;
    const cfg = getStateVisualConfig(sphereState, theme);
    const metrics = audioEngine.sample(dtMs, micLevel);
    setAudioMetrics(metrics);

    const lerpAlpha = Math.min(1, dt / 0.35);
    targetA.set(cfg.colorA);
    targetB.set(cfg.colorB);
    targetAccent.set(cfg.colorAccent);

    uniforms.uColorA.value.lerp(targetA, lerpAlpha);
    uniforms.uColorB.value.lerp(targetB, lerpAlpha);
    uniforms.uColorAccent.value.lerp(targetAccent, lerpAlpha);
    uniforms.uState.value = THREE.MathUtils.lerp(uniforms.uState.value, cfg.stateIndex, lerpAlpha);
    uniforms.uNoiseScale.value = THREE.MathUtils.lerp(
      uniforms.uNoiseScale.value,
      cfg.noiseScale,
      lerpAlpha
    );
    uniforms.uNoiseSpeed.value = THREE.MathUtils.lerp(
      uniforms.uNoiseSpeed.value,
      cfg.noiseSpeed,
      lerpAlpha
    );

    if (wireMatRef.current) {
      wireMatRef.current.color.lerp(targetB, lerpAlpha);
    }
    pointUniforms.uColor.value.lerp(targetAccent, lerpAlpha);
    pointUniforms.uOpacity.value = THREE.MathUtils.lerp(
      pointUniforms.uOpacity.value,
      sphereState === "offline" ? 0.22 : 0.34,
      lerpAlpha
    );
    if (ringOuterMatRef.current) {
      ringOuterMatRef.current.color.lerp(targetAccent, lerpAlpha);
    }
    if (ringInnerMatRef.current) {
      ringInnerMatRef.current.color.lerp(targetB, lerpAlpha);
    }

    const motionScale = reducedMotion ? 0.15 : 1.0;
    if (sphereState !== "offline") {
      uniforms.uTime.value += dt * motionScale;
    }

    const isSpeaking = sphereState === "speaking";
    // Modulation et vibration dynamique au rythme des paroles de Jarvis
    const speechCadence = isSpeaking
      ? Math.max(
          metrics.amplitude,
          0.38 + 0.3 * Math.sin(uniforms.uTime.value * 16.0) * Math.cos(uniforms.uTime.value * 9.5)
        ) * motionScale
      : 0;
    const speechJitter = isSpeaking
      ? Math.sin(uniforms.uTime.value * 52.0) * speechCadence * 0.06
      : 0;
    const speechBounce = isSpeaking
      ? (Math.sin(uniforms.uTime.value * 14.0) * speechCadence * 0.05 + Math.cos(uniforms.uTime.value * 28.0) * speechCadence * 0.02)
      : 0;

    const activeAmp =
      sphereState === "alert"
        ? 0.25 + 0.15 * Math.sin(uniforms.uTime.value * 4.5)
        : isSpeaking
        ? Math.max(metrics.amplitude, speechCadence * 0.9)
        : metrics.amplitude;

    uniforms.uAmplitude.value = activeAmp * motionScale;
    uniforms.uLow.value = metrics.low * motionScale;
    uniforms.uMid.value = metrics.mid * motionScale;
    uniforms.uHigh.value = metrics.high * motionScale;

    const px = reducedMotion ? 0 : pointerX.get();
    const py = reducedMotion ? 0 : pointerY.get();
    uniforms.uPointer.value.set(px, py);

    if (parallaxGroupRef.current) {
      // Impact souris fortement diminué (mouvement subtil et fluide) + vibration verticale de la voix
      parallaxGroupRef.current.position.x = px * 0.035;
      parallaxGroupRef.current.position.y = py * 0.028 + speechBounce;
      parallaxGroupRef.current.rotation.x = -py * 0.12;
      parallaxGroupRef.current.rotation.y = px * 0.14;
    }

    if (sphereState !== "offline") {
      if (coreMeshRef.current) {
        coreMeshRef.current.rotation.y += dt * (cfg.coreSpeed + (isSpeaking ? speechCadence * 1.6 : 0)) * motionScale;
        coreMeshRef.current.rotation.x = Math.sin(uniforms.uTime.value * 2.2) * 0.05 + (isSpeaking ? Math.sin(uniforms.uTime.value * 34.0) * speechCadence * 0.04 : 0);
        coreMeshRef.current.rotation.z = Math.sin(uniforms.uTime.value * 0.4) * 0.1 + (isSpeaking ? Math.cos(uniforms.uTime.value * 28.0) * speechCadence * 0.03 : 0);

        // La sphère principale grossit, vibre et pulse au rythme des paroles
        const coreScale = 1.0 + (isSpeaking ? speechCadence * 0.24 + speechJitter * 1.8 : activeAmp * 0.05);
        coreMeshRef.current.scale.set(coreScale, coreScale, coreScale);
      }

      const shellScale = 1.0 + (isSpeaking ? speechCadence * 0.24 + speechJitter * 1.6 : activeAmp * 0.04);
      if (wireMeshRef.current) {
        wireMeshRef.current.rotation.y -= dt * cfg.ringSpeed * (isSpeaking ? 0.65 : 0.35) * motionScale;
        wireMeshRef.current.rotation.x += dt * cfg.ringSpeed * (isSpeaking ? 0.35 : 0.18) * motionScale;
        wireMeshRef.current.scale.set(shellScale, shellScale, shellScale);
        if (wireMatRef.current) {
          wireMatRef.current.opacity = 0.22 + (isSpeaking ? speechCadence * 0.35 : 0);
        }
      }
      if (pointsRef.current && wireMeshRef.current) {
        pointsRef.current.rotation.copy(wireMeshRef.current.rotation);
        pointsRef.current.scale.set(shellScale, shellScale, shellScale);
      }

      const thinkingBoost = sphereState === "thinking" ? 1.55 : isSpeaking ? 1.6 + speechCadence * 2.5 : 1.0;
      if (ringOuterRef.current) {
        ringOuterRef.current.rotation.z += dt * cfg.ringSpeed * 0.65 * thinkingBoost * motionScale;
        ringOuterRef.current.rotation.x =
          0.72 + py * 0.05 + Math.sin(uniforms.uTime.value * 0.5) * 0.08 + speechJitter * 1.4;
        ringOuterRef.current.rotation.y = px * 0.05 + uniforms.uTime.value * 0.22 * cfg.ringSpeed;
        ringOuterRef.current.position.x = px * 0.01;
        ringOuterRef.current.position.y = py * 0.008;

        // Vibration et expansion de l'anneau externe
        const ringOuterScale =
          1.0 + (isSpeaking ? speechCadence * 0.28 + speechJitter * 2.4 : activeAmp * 0.035);
        ringOuterRef.current.scale.set(ringOuterScale, ringOuterScale, ringOuterScale);
        if (ringOuterMatRef.current) {
          ringOuterMatRef.current.opacity = 0.42 + (isSpeaking ? speechCadence * 0.5 : 0);
        }
      }

      if (ringInnerRef.current) {
        ringInnerRef.current.rotation.z -= dt * cfg.ringSpeed * 0.85 * thinkingBoost * motionScale;
        ringInnerRef.current.rotation.x = -0.58 + py * 0.04 - speechJitter * 1.0;
        ringInnerRef.current.rotation.y = -px * 0.05 - uniforms.uTime.value * 0.28 * cfg.ringSpeed;
        ringInnerRef.current.position.x = px * 0.012;
        ringInnerRef.current.position.y = py * 0.01;

        // Vibration de l'anneau intérieur
        const ringInnerScale =
          1.0 + (isSpeaking ? speechCadence * 0.22 - speechJitter * 1.8 : activeAmp * 0.03);
        ringInnerRef.current.scale.set(ringInnerScale, ringInnerScale, ringInnerScale);
        if (ringInnerMatRef.current) {
          ringInnerMatRef.current.opacity = 0.28 + (isSpeaking ? speechCadence * 0.45 : 0);
        }
      }
    }
  });

  return (
    <group ref={parallaxGroupRef}>
      <mesh ref={coreMeshRef}>
        <icosahedronGeometry args={[1.18, 20]} />
        <shaderMaterial
          vertexShader={sphereVertexShader}
          fragmentShader={sphereFragmentShader}
          uniforms={uniforms}
          transparent
        />
      </mesh>
      <mesh ref={wireMeshRef}>
        <icosahedronGeometry args={[1.34, 4]} />
        <meshBasicMaterial
          ref={wireMatRef}
          color="#38bdf8"
          wireframe
          transparent
          opacity={0.22}
          depthWrite={false}
        />
      </mesh>
      <points ref={pointsRef}>
        <icosahedronGeometry args={[1.345, 4]} />
        <shaderMaterial
          vertexShader={pointVertexShader}
          fragmentShader={pointFragmentShader}
          uniforms={pointUniforms}
          transparent
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </points>
      <mesh ref={ringOuterRef}>
        <torusGeometry args={[1.58, 0.008, 16, 128]} />
        <meshBasicMaterial
          ref={ringOuterMatRef}
          color="#7dd3fc"
          transparent
          opacity={0.42}
        />
      </mesh>
      <mesh ref={ringInnerRef}>
        <torusGeometry args={[1.68, 0.006, 16, 128]} />
        <meshBasicMaterial
          ref={ringInnerMatRef}
          color="#38bdf8"
          transparent
          opacity={0.28}
        />
      </mesh>
    </group>
  );
}

interface VoiceSphereProps {
  audioEngine: AudioEngine;
}

export function VoiceSphere({ audioEngine }: VoiceSphereProps) {
  const sphereState = useJarvisStore((s) => s.sphereState);
  const theme = useJarvisStore((s) => s.theme);
  const amplitude = useJarvisStore((s) => s.audio.amplitude);

  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const [webglReady, setWebglReady] = useState<boolean>(() => detectWebGL());
  const [tabVisible, setTabVisible] = useState<boolean>(
    () => typeof document === "undefined" || !document.hidden
  );
  const [isForeground, setIsForeground] = useState<boolean>(
    () => typeof document === "undefined" || (!document.hidden && document.hasFocus())
  );
  const [reducedMotion, setReducedMotion] = useState<boolean>(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches
  );

  const rawPointerX = useMotionValue(0);
  const rawPointerY = useMotionValue(0);
  const smoothPointerX = useSpring(rawPointerX, { stiffness: 110, damping: 20, mass: 0.6 });
  const smoothPointerY = useSpring(rawPointerY, { stiffness: 110, damping: 20, mass: 0.6 });

  const wrapperX = useTransform(smoothPointerX, [-1, 1], [-2.5, 2.5]);
  const wrapperY = useTransform(smoothPointerY, [-1, 1], [2.5, -2.5]);

  useEffect(() => {
    setWebglReady(detectWebGL());

    const syncForeground = () => {
      const visible = !document.hidden;
      const focused = typeof document.hasFocus === "function" ? document.hasFocus() : true;
      const active = visible && focused;
      setTabVisible(visible);
      setIsForeground(active);
      if (!active) {
        rawPointerX.set(0);
        rawPointerY.set(0);
      }
    };

    const resetPointer = () => {
      rawPointerX.set(0);
      rawPointerY.set(0);
    };

    const onPointerMove = (e: PointerEvent) => {
      if (document.hidden || (typeof document.hasFocus === "function" && !document.hasFocus()) || reducedMotion) {
        resetPointer();
        return;
      }
      const rect = wrapperRef.current?.getBoundingClientRect();
      const cx = rect && rect.width > 0 ? rect.left + rect.width / 2 : window.innerWidth / 2;
      const cy = rect && rect.height > 0 ? rect.top + rect.height / 2 : window.innerHeight / 2;
      const spanX = Math.max(window.innerWidth * 0.5, rect ? rect.width * 1.6 : 300);
      const spanY = Math.max(window.innerHeight * 0.5, rect ? rect.height * 1.6 : 300);

      // Amplitude réduite du pointeur pour ne pas brusquer la sphère
      const nx = Math.max(-1, Math.min(1, (e.clientX - cx) / spanX)) * 0.35;
      const ny = Math.max(-1, Math.min(1, -(e.clientY - cy) / spanY)) * 0.35;
      rawPointerX.set(nx);
      rawPointerY.set(ny);
    };

    const mq = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    const onMotion = () => {
      const next = Boolean(mq?.matches);
      setReducedMotion(next);
      if (next) resetPointer();
    };

    document.addEventListener("visibilitychange", syncForeground);
    window.addEventListener("focus", syncForeground);
    window.addEventListener("blur", syncForeground);
    window.addEventListener("pointermove", onPointerMove, { passive: true });
    document.documentElement.addEventListener("mouseleave", resetPointer);
    mq?.addEventListener?.("change", onMotion);

    return () => {
      document.removeEventListener("visibilitychange", syncForeground);
      window.removeEventListener("focus", syncForeground);
      window.removeEventListener("blur", syncForeground);
      window.removeEventListener("pointermove", onPointerMove);
      document.documentElement.removeEventListener("mouseleave", resetPointer);
      mq?.removeEventListener?.("change", onMotion);
    };
  }, [rawPointerX, rawPointerY, reducedMotion]);

  const fallback = (
    <SphereFallback2D
      sphereState={sphereState}
      theme={theme}
      amplitude={amplitude}
      reducedMotion={reducedMotion}
      pointerX={smoothPointerX}
      pointerY={smoothPointerY}
    />
  );

  return (
    <motion.div
      ref={wrapperRef}
      className="sphere-wrapper"
      data-testid="jarvis-sphere"
      data-sphere-state={sphereState}
      data-renderer={webglReady ? "webgl" : "canvas2d"}
      data-foreground={isForeground}
      style={reducedMotion ? undefined : { x: wrapperX, y: wrapperY }}
    >
      {webglReady ? (
        <CanvasErrorBoundary fallback={fallback}>
          <Canvas
            dpr={[1, 2]}
            frameloop={tabVisible ? "always" : "never"}
            camera={{ position: [0, 0, 4.85], fov: 45 }}
            gl={{ antialias: true, alpha: true, premultipliedAlpha: true, powerPreference: "default" }}
            onCreated={({ gl }) => {
              if (!gl.getContext()) {
                setWebglReady(false);
                return;
              }
              gl.setClearColor(0x000000, 0);
              const canvas = gl.domElement;
              canvas.addEventListener(
                "webglcontextlost",
                (e) => {
                  e.preventDefault();
                  setWebglReady(false);
                },
                { once: true }
              );
              canvas.addEventListener(
                "webglcontextrestored",
                () => setWebglReady(true),
                { once: true }
              );
            }}
          >
            <SphereCoreMesh
              audioEngine={audioEngine}
              reducedMotion={reducedMotion}
              pointerX={smoothPointerX}
              pointerY={smoothPointerY}
            />
          </Canvas>
        </CanvasErrorBoundary>
      ) : (
        fallback
      )}
    </motion.div>
  );
}
