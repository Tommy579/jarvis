import {
  Component,
  type ErrorInfo,
  type ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { AnimatePresence, motion } from "motion/react";
import * as THREE from "three";
import { apiClient } from "../api/client";
import type { AudioEngine } from "../sphere/AudioEngine";
import { useJarvisStore } from "../store/useJarvisStore";
import type {
  VisualArtifactPayload,
  VisualElementPayload,
  VisualStepPayload,
} from "../types/generated";
import { Icon } from "./Icon";

interface VisualArtifactWindowProps {
  audioEngine: AudioEngine;
}

const CATEGORY_LABELS: Record<VisualArtifactPayload["category"], string> = {
  hardware: "Hardware & Circuits",
  architecture: "Architecture",
  algorithm: "Algorithme",
  physics: "Physique",
  custom: "Motion",
};

function detectWebGLSupport(): boolean {
  if (typeof window === "undefined") return false;
  try {
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
    if (!gl) return false;
    gl.getExtension("WEBGL_lose_context")?.loseContext();
    return true;
  } catch {
    return false;
  }
}

class Scene3DErrorBoundary extends Component<
  { fallback: ReactNode; children: ReactNode },
  { hasError: boolean }
> {
  state = { hasError: false };
  static getDerivedStateFromError() {
    return { hasError: true };
  }
  componentDidCatch(_err: Error, _info: ErrorInfo) {
    // Repli automatique sur la vue 2D Motion
  }
  render() {
    return this.state.hasError ? this.props.fallback : this.props.children;
  }
}

function getCumulativeState(
  artifact: VisualArtifactPayload,
  stepIndex: number
): {
  visibleIds: Set<string>;
  highlightedIds: Set<string>;
  activeConnIds: Set<string>;
  currentStep: VisualStepPayload | null;
} {
  const steps = artifact.steps;
  if (steps.length === 0) {
    return {
      visibleIds: new Set(artifact.elements.map((e) => e.id)),
      highlightedIds: new Set(),
      activeConnIds: new Set(),
      currentStep: null,
    };
  }
  const safeIdx = Math.max(0, Math.min(steps.length - 1, stepIndex));
  const visibleIds = new Set<string>();
  for (let i = 0; i <= safeIdx; i += 1) {
    for (const eid of steps[i].reveal_elements) {
      visibleIds.add(eid);
    }
  }
  const currentStep = steps[safeIdx];
  for (const eid of currentStep.highlight_elements) {
    visibleIds.add(eid);
  }
  for (const eid of currentStep.active_connections) {
    visibleIds.add(eid);
  }
  return {
    visibleIds,
    highlightedIds: new Set(currentStep.highlight_elements),
    activeConnIds: new Set(currentStep.active_connections),
    currentStep,
  };
}

function resolveAnchor(
  el: VisualElementPayload,
  byId: Map<string, VisualElementPayload>,
  fallbackX: number,
  fallbackY: number,
  isFrom: boolean
): { x: number; y: number } {
  const refId = isFrom ? el.from_id : el.to_id;
  if (refId && byId.has(refId)) {
    const target = byId.get(refId)!;
    return { x: target.x, y: target.y };
  }
  return { x: fallbackX, y: fallbackY };
}

/* ============================================================================
 *  Rendu 2D Motion (SVG CAD + Framer Motion)
 * ========================================================================== */

function Scene2DMotion({
  artifact,
  visibleIds,
  highlightedIds,
  activeConnIds,
}: {
  artifact: VisualArtifactPayload;
  visibleIds: Set<string>;
  highlightedIds: Set<string>;
  activeConnIds: Set<string>;
}) {
  const byId = useMemo(
    () => new Map(artifact.elements.map((el) => [el.id, el])),
    [artifact.elements]
  );

  const visibleElements = artifact.elements.filter((el) => visibleIds.has(el.id));
  const connections = visibleElements.filter(
    (el) => el.kind === "wire" || el.kind === "arrow"
  );
  const components = visibleElements.filter(
    (el) => el.kind !== "wire" && el.kind !== "arrow"
  );

  const W = 1000;
  const H = 620;
  const px = (x: number) => (x / 100) * W;
  const py = (y: number) => (y / 100) * H;

  return (
    <div className="artifact-canvas artifact-canvas--2d" data-testid="artifact-2d-view">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="artifact-svg"
        role="img"
        aria-label={artifact.title}
      >
        <defs>
          <pattern id="jarvis-grid-minor" width="25" height="25" patternUnits="userSpaceOnUse">
            <path
              d="M 25 0 L 0 0 0 25"
              fill="none"
              stroke="rgba(56, 189, 248, 0.08)"
              strokeWidth="1"
            />
          </pattern>
          <pattern id="jarvis-grid-major" width="100" height="100" patternUnits="userSpaceOnUse">
            <rect width="100" height="100" fill="url(#jarvis-grid-minor)" />
            <path
              d="M 100 0 L 0 0 0 100"
              fill="none"
              stroke="rgba(56, 189, 248, 0.18)"
              strokeWidth="1"
            />
          </pattern>
          <filter id="jarvis-glow" x="-40%" y="-40%" width="180%" height="180%">
            <feGaussianBlur stdDeviation="6" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
          <marker
            id="jarvis-arrowhead"
            markerWidth="10"
            markerHeight="7"
            refX="9"
            refY="3.5"
            orient="auto"
          >
            <polygon points="0 0, 10 3.5, 0 7" fill="#38bdf8" />
          </marker>
        </defs>

        <rect width={W} height={H} fill="url(#jarvis-grid-major)" />

        {/* Connexions (câbles / flèches) */}
        <g>
          {connections.map((conn) => {
            const p1 = resolveAnchor(conn, byId, conn.x - 10, conn.y, true);
            const p2 = resolveAnchor(conn, byId, conn.x + 10, conn.y, false);
            const x1 = px(p1.x);
            const y1 = py(p1.y);
            const x2 = px(p2.x);
            const y2 = py(p2.y);
            const dx = (x2 - x1) * 0.45;
            const d = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
            const isHighlighted = highlightedIds.has(conn.id);
            const isActive = activeConnIds.has(conn.id);
            const strokeColor = conn.color || "#38bdf8";

            return (
              <g key={conn.id} data-element-id={conn.id}>
                <motion.path
                  d={d}
                  fill="none"
                  stroke={strokeColor}
                  strokeWidth={isHighlighted || isActive ? 4 : 2.6}
                  strokeLinecap="round"
                  markerEnd={conn.kind === "arrow" ? "url(#jarvis-arrowhead)" : undefined}
                  filter={isHighlighted || isActive ? "url(#jarvis-glow)" : undefined}
                  initial={{ pathLength: 0, opacity: 0.2 }}
                  animate={{ pathLength: 1, opacity: 1 }}
                  transition={{ duration: 0.65, ease: [0.22, 1, 0.36, 1] }}
                />
                {isActive && (
                  <motion.path
                    d={d}
                    fill="none"
                    stroke="#ffffff"
                    strokeWidth={3}
                    strokeDasharray="10 18"
                    strokeLinecap="round"
                    initial={{ strokeDashoffset: 56 }}
                    animate={{ strokeDashoffset: 0 }}
                    transition={{ duration: 0.9, repeat: Infinity, ease: "linear" }}
                  />
                )}
                {(conn.label || conn.from_pin || conn.to_pin) && (
                  <g transform={`translate(${(x1 + x2) / 2}, ${(y1 + y2) / 2 - 12})`}>
                    <rect
                      x={-68}
                      y={-12}
                      width={136}
                      height={22}
                      rx={6}
                      fill="rgba(9, 15, 24, 0.88)"
                      stroke={strokeColor}
                      strokeOpacity={0.6}
                      strokeWidth={1}
                    />
                    <text
                      textAnchor="middle"
                      y={3}
                      fill="#e2e8f0"
                      fontSize="11"
                      fontFamily="var(--font-mono)"
                    >
                      {conn.from_pin && conn.to_pin
                        ? `${conn.from_pin} → ${conn.to_pin}`
                        : conn.label}
                    </text>
                  </g>
                )}
              </g>
            );
          })}
        </g>

        {/* Composants & Nœuds */}
        <AnimatePresence>
          {components.map((el) => {
            const cx = px(el.x);
            const cy = py(el.y);
            const w = Math.max(48, (el.width / 100) * W);
            const h = Math.max(36, (el.height / 100) * H);
            const isHighlighted = highlightedIds.has(el.id);
            const isPowered =
              el.kind === "led" &&
              (isHighlighted ||
                connections.some(
                  (c) =>
                    activeConnIds.has(c.id) && (c.to_id === el.id || c.from_id === el.id)
                ));
            const color = el.color || "#38bdf8";

            return (
              <motion.g
                key={el.id}
                data-element-id={el.id}
                initial={{ opacity: 0, scale: 0.82 }}
                animate={{
                  opacity: 1,
                  scale: isHighlighted ? 1.06 : 1,
                }}
                exit={{ opacity: 0, scale: 0.85 }}
                transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
                style={{ transformOrigin: `${cx}px ${cy}px` }}
              >
                {isHighlighted && (
                  <motion.rect
                    x={cx - w / 2 - 10}
                    y={cy - h / 2 - 10}
                    width={w + 20}
                    height={h + 20}
                    rx={14}
                    fill="none"
                    stroke="#38bdf8"
                    strokeWidth={2}
                    strokeDasharray="6 4"
                    animate={{ opacity: [0.35, 1, 0.35] }}
                    transition={{ duration: 1.4, repeat: Infinity }}
                  />
                )}

                {el.kind === "board" && (
                  <g>
                    <rect
                      x={cx - w / 2}
                      y={cy - h / 2}
                      width={w}
                      height={h}
                      rx={12}
                      fill="#0f3d2e"
                      stroke={color}
                      strokeWidth={2}
                    />
                    {/* Rail GPIO */}
                    <rect
                      x={cx - w / 2 + 12}
                      y={cy - h / 2 + 10}
                      width={w - 24}
                      height={14}
                      rx={3}
                      fill="#111827"
                      stroke="#94a3b8"
                      strokeWidth={1}
                    />
                    {Array.from({ length: 10 }).map((_, idx) => (
                      <circle
                        key={idx}
                        cx={cx - w / 2 + 22 + idx * ((w - 44) / 9)}
                        cy={cy - h / 2 + 17}
                        r={2.5}
                        fill="#facc15"
                      />
                    ))}
                    {/* SoC */}
                    <rect
                      x={cx - 18}
                      y={cy - 4}
                      width={36}
                      height={28}
                      rx={4}
                      fill="#1e293b"
                      stroke="#64748b"
                    />
                  </g>
                )}

                {el.kind === "led" && (
                  <g filter={isPowered ? "url(#jarvis-glow)" : undefined}>
                    {isPowered && (
                      <circle cx={cx} cy={cy} r={34} fill={color} fillOpacity={0.28} />
                    )}
                    <line x1={cx - 8} y1={cy + 14} x2={cx - 8} y2={cy + 30} stroke="#94a3b8" strokeWidth={2.5} />
                    <line x1={cx + 8} y1={cy + 14} x2={cx + 8} y2={cy + 26} stroke="#94a3b8" strokeWidth={2.5} />
                    <path
                      d={`M ${cx - 16} ${cy + 12} L ${cx - 16} ${cy - 4} A 16 16 0 0 1 ${cx + 16} ${cy - 4} L ${cx + 16} ${cy + 12} Z`}
                      fill={color}
                      fillOpacity={isPowered ? 0.95 : 0.55}
                      stroke="#ffffff"
                      strokeWidth={1.6}
                    />
                  </g>
                )}

                {el.kind === "resistor" && (
                  <g>
                    <line x1={cx - 44} y1={cy} x2={cx + 44} y2={cy} stroke="#cbd5e1" strokeWidth={3} />
                    <rect
                      x={cx - 26}
                      y={cy - 10}
                      width={52}
                      height={20}
                      rx={6}
                      fill="#e7cba9"
                      stroke="#78350f"
                      strokeWidth={1.5}
                    />
                    <rect x={cx - 16} y={cy - 10} width={5} height={20} fill="#ea580c" />
                    <rect x={cx - 5} y={cy - 10} width={5} height={20} fill="#ea580c" />
                    <rect x={cx + 6} y={cy - 10} width={5} height={20} fill="#78350f" />
                    <rect x={cx + 16} y={cy - 10} width={4} height={20} fill="#eab308" />
                  </g>
                )}

                {el.kind === "svg_path" && el.svg_path && (
                  <g transform={`translate(${cx - 24}, ${cy - 24})`}>
                    <path d={el.svg_path} fill="none" stroke={color} strokeWidth={2.5} />
                  </g>
                )}

                {el.kind !== "board" &&
                  el.kind !== "led" &&
                  el.kind !== "resistor" &&
                  el.kind !== "svg_path" && (
                    <g>
                      {el.kind === "sphere" ? (
                        <circle
                          cx={cx}
                          cy={cy}
                          r={Math.min(w, h) * 0.48}
                          fill="rgba(15, 23, 42, 0.9)"
                          stroke={color}
                          strokeWidth={2}
                        />
                      ) : (
                        <rect
                          x={cx - w / 2}
                          y={cy - h / 2}
                          width={w}
                          height={h}
                          rx={el.kind === "button" ? 18 : 10}
                          fill="rgba(15, 23, 42, 0.9)"
                          stroke={color}
                          strokeWidth={2}
                        />
                      )}
                    </g>
                  )}

                {/* Libellé */}
                <text
                  x={cx}
                  y={cy + h / 2 + 18}
                  textAnchor="middle"
                  fill="#f8fafc"
                  fontSize="13"
                  fontWeight={600}
                  fontFamily="var(--font-sans)"
                >
                  {el.label}
                </text>
                {el.sublabel && (
                  <text
                    x={cx}
                    y={cy + h / 2 + 34}
                    textAnchor="middle"
                    fill="#94a3b8"
                    fontSize="11"
                    fontFamily="var(--font-mono)"
                  >
                    {el.sublabel}
                  </text>
                )}
              </motion.g>
            );
          })}
        </AnimatePresence>
      </svg>
    </div>
  );
}

/* ============================================================================
 *  Rendu 3D Interactif (@react-three/fiber + Three.js)
 * ========================================================================== */

function toWorldPos(el: VisualElementPayload): THREE.Vector3 {
  return new THREE.Vector3((el.x - 50) * 0.14, (el.z || 0) * 0.08, (el.y - 50) * 0.09);
}

function CameraRig({
  focusTarget,
  orbit,
}: {
  focusTarget: THREE.Vector3;
  orbit: { theta: number; phi: number; distance: number };
}) {
  const { camera } = useThree();
  const currentLook = useRef(new THREE.Vector3(0, 0, 0));

  useFrame((_, delta) => {
    const alpha = Math.min(1, delta * 4.5);
    currentLook.current.lerp(focusTarget, alpha);
    const sinPhi = Math.sin(orbit.phi);
    const desiredPos = new THREE.Vector3(
      currentLook.current.x + orbit.distance * sinPhi * Math.sin(orbit.theta),
      currentLook.current.y + orbit.distance * Math.cos(orbit.phi),
      currentLook.current.z + orbit.distance * sinPhi * Math.cos(orbit.theta)
    );
    camera.position.lerp(desiredPos, alpha);
    camera.lookAt(currentLook.current);
  });

  return null;
}

function WireMesh3D({
  from,
  to,
  color,
  isActive,
}: {
  from: THREE.Vector3;
  to: THREE.Vector3;
  color: string;
  isActive: boolean;
}) {
  const pulseRef = useRef<THREE.Mesh | null>(null);
  const { curve, geometry } = useMemo(() => {
    const mid = from.clone().add(to).multiplyScalar(0.5);
    mid.y += Math.max(0.7, from.distanceTo(to) * 0.28);
    const c = new THREE.QuadraticBezierCurve3(from, mid, to);
    const geom = new THREE.TubeGeometry(c, 28, isActive ? 0.065 : 0.045, 8, false);
    return { curve: c, geometry: geom };
  }, [from, to, isActive]);

  useEffect(() => {
    return () => geometry.dispose();
  }, [geometry]);

  useFrame(({ clock }) => {
    if (!isActive || !pulseRef.current) return;
    const t = (clock.getElapsedTime() * 0.65) % 1;
    const pt = curve.getPoint(t);
    pulseRef.current.position.copy(pt);
  });

  return (
    <group>
      <mesh geometry={geometry}>
        <meshStandardMaterial
          color={color}
          emissive={isActive ? color : "#000000"}
          emissiveIntensity={isActive ? 0.75 : 0}
          roughness={0.35}
        />
      </mesh>
      {isActive && (
        <mesh ref={pulseRef}>
          <sphereGeometry args={[0.13, 12, 12]} />
          <meshBasicMaterial color="#ffffff" />
        </mesh>
      )}
    </group>
  );
}

function ComponentMesh3D({
  el,
  isHighlighted,
  isPowered,
}: {
  el: VisualElementPayload;
  isHighlighted: boolean;
  isPowered: boolean;
}) {
  const pos = toWorldPos(el);
  const w = Math.max(0.7, el.width * 0.12);
  const d = Math.max(0.5, el.height * 0.08);
  const color = el.color || "#38bdf8";
  const ringRef = useRef<THREE.Mesh | null>(null);

  useFrame(({ clock }) => {
    if (ringRef.current && isHighlighted) {
      const s = 1 + Math.sin(clock.getElapsedTime() * 4) * 0.08;
      ringRef.current.scale.set(s, s, 1);
    }
  });

  return (
    <group position={[pos.x, pos.y, pos.z]}>
      {isHighlighted && (
        <mesh ref={ringRef} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.02, 0]}>
          <ringGeometry args={[Math.max(w, d) * 0.62, Math.max(w, d) * 0.72, 32]} />
          <meshBasicMaterial color="#38bdf8" transparent opacity={0.85} side={THREE.DoubleSide} />
        </mesh>
      )}

      {el.kind === "board" && (
        <group>
          <mesh position={[0, 0.12, 0]}>
            <boxGeometry args={[w, 0.16, d]} />
            <meshStandardMaterial color="#0f4c3a" roughness={0.4} metalness={0.2} />
          </mesh>
          {/* SoC */}
          <mesh position={[0, 0.24, 0.1]}>
            <boxGeometry args={[w * 0.25, 0.1, d * 0.28]} />
            <meshStandardMaterial color="#1e293b" roughness={0.3} metalness={0.6} />
          </mesh>
          {/* Header GPIO */}
          <mesh position={[0, 0.26, -d * 0.34]}>
            <boxGeometry args={[w * 0.78, 0.14, 0.22]} />
            <meshStandardMaterial color="#111827" />
          </mesh>
          {Array.from({ length: 10 }).map((_, idx) => (
            <mesh
              key={idx}
              position={[-w * 0.34 + idx * ((w * 0.68) / 9), 0.38, -d * 0.34]}
            >
              <cylinderGeometry args={[0.025, 0.025, 0.18, 8]} />
              <meshStandardMaterial color="#facc15" metalness={0.85} roughness={0.2} />
            </mesh>
          ))}
        </group>
      )}

      {el.kind === "led" && (
        <group>
          <mesh position={[-0.12, 0.22, 0]}>
            <cylinderGeometry args={[0.025, 0.025, 0.45, 8]} />
            <meshStandardMaterial color="#cbd5e1" metalness={0.9} roughness={0.2} />
          </mesh>
          <mesh position={[0.12, 0.22, 0]}>
            <cylinderGeometry args={[0.025, 0.025, 0.45, 8]} />
            <meshStandardMaterial color="#cbd5e1" metalness={0.9} roughness={0.2} />
          </mesh>
          <mesh position={[0, 0.52, 0]}>
            <cylinderGeometry args={[0.26, 0.28, 0.3, 18]} />
            <meshStandardMaterial
              color={color}
              emissive={color}
              emissiveIntensity={isPowered ? 1.6 : 0.25}
              transparent
              opacity={0.88}
            />
          </mesh>
          <mesh position={[0, 0.67, 0]}>
            <sphereGeometry args={[0.26, 18, 18]} />
            <meshStandardMaterial
              color={color}
              emissive={color}
              emissiveIntensity={isPowered ? 1.8 : 0.25}
              transparent
              opacity={0.88}
            />
          </mesh>
          {isPowered && <pointLight position={[0, 0.85, 0]} color={color} intensity={2.2} distance={4} />}
        </group>
      )}

      {el.kind === "resistor" && (
        <group>
          <mesh rotation={[0, 0, Math.PI / 2]} position={[0, 0.22, 0]}>
            <cylinderGeometry args={[0.025, 0.025, 1.3, 8]} />
            <meshStandardMaterial color="#cbd5e1" metalness={0.85} roughness={0.25} />
          </mesh>
          <mesh rotation={[0, 0, Math.PI / 2]} position={[0, 0.22, 0]}>
            <cylinderGeometry args={[0.14, 0.14, 0.65, 14]} />
            <meshStandardMaterial color="#e7cba9" roughness={0.5} />
          </mesh>
          {[-0.18, -0.05, 0.08, 0.2].map((ox, idx) => (
            <mesh key={idx} rotation={[0, 0, Math.PI / 2]} position={[ox, 0.22, 0]}>
              <cylinderGeometry args={[0.146, 0.146, 0.06, 14]} />
              <meshStandardMaterial color={idx === 2 ? "#78350f" : "#ea580c"} />
            </mesh>
          ))}
        </group>
      )}

      {el.kind !== "board" && el.kind !== "led" && el.kind !== "resistor" && (
        <mesh position={[0, 0.28, 0]}>
          {el.kind === "sphere" ? (
            <sphereGeometry args={[Math.min(w, d) * 0.45, 20, 20]} />
          ) : el.kind === "cylinder" ? (
            <cylinderGeometry args={[Math.min(w, d) * 0.4, Math.min(w, d) * 0.4, 0.55, 20]} />
          ) : (
            <boxGeometry args={[w * 0.85, 0.42, d * 0.85]} />
          )}
          <meshStandardMaterial
            color={color}
            emissive={isHighlighted ? color : "#000000"}
            emissiveIntensity={isHighlighted ? 0.55 : 0.12}
            roughness={0.35}
            metalness={0.3}
          />
        </mesh>
      )}
    </group>
  );
}

function Scene3DInteractive({
  artifact,
  visibleIds,
  highlightedIds,
  activeConnIds,
}: {
  artifact: VisualArtifactPayload;
  visibleIds: Set<string>;
  highlightedIds: Set<string>;
  activeConnIds: Set<string>;
}) {
  const [orbit, setOrbit] = useState({ theta: 0.35, phi: 0.92, distance: 10.5 });
  const dragRef = useRef<{ active: boolean; x: number; y: number }>({
    active: false,
    x: 0,
    y: 0,
  });

  const byId = useMemo(
    () => new Map(artifact.elements.map((el) => [el.id, el])),
    [artifact.elements]
  );

  const visibleElements = artifact.elements.filter((el) => visibleIds.has(el.id));
  const connections = visibleElements.filter(
    (el) => el.kind === "wire" || el.kind === "arrow"
  );
  const components = visibleElements.filter(
    (el) => el.kind !== "wire" && el.kind !== "arrow"
  );

  const focusTarget = useMemo(() => {
    const targets = components.filter((el) => highlightedIds.has(el.id));
    if (targets.length === 0) return new THREE.Vector3(0, 0.2, 0);
    const sum = new THREE.Vector3();
    for (const t of targets) {
      sum.add(toWorldPos(t));
    }
    return sum.divideScalar(targets.length);
  }, [components, highlightedIds]);

  return (
    <div
      className="artifact-canvas artifact-canvas--3d"
      data-testid="artifact-3d-view"
      onPointerDown={(e) => {
        dragRef.current = { active: true, x: e.clientX, y: e.clientY };
      }}
      onPointerMove={(e) => {
        if (!dragRef.current.active) return;
        const dx = e.clientX - dragRef.current.x;
        const dy = e.clientY - dragRef.current.y;
        dragRef.current = { active: true, x: e.clientX, y: e.clientY };
        setOrbit((prev) => ({
          ...prev,
          theta: prev.theta - dx * 0.007,
          phi: Math.max(0.25, Math.min(1.45, prev.phi - dy * 0.006)),
        }));
      }}
      onPointerUp={() => {
        dragRef.current.active = false;
      }}
      onPointerLeave={() => {
        dragRef.current.active = false;
      }}
      onWheel={(e) => {
        setOrbit((prev) => ({
          ...prev,
          distance: Math.max(5.5, Math.min(18, prev.distance + e.deltaY * 0.008)),
        }));
      }}
    >
      <Canvas dpr={[1, 2]} camera={{ position: [3, 5.5, 8.5], fov: 42 }}>
        <color attach="background" args={["#070d16"]} />
        <ambientLight intensity={0.7} />
        <directionalLight position={[6, 10, 5]} intensity={1.3} color="#e0f2fe" />
        <directionalLight position={[-6, 4, -5]} intensity={0.5} color="#38bdf8" />
        <gridHelper args={[18, 24, "#1e3a5f", "#0f2035"]} />

        <CameraRig focusTarget={focusTarget} orbit={orbit} />

        {components.map((el) => {
          const isHighlighted = highlightedIds.has(el.id);
          const isPowered =
            el.kind === "led" &&
            (isHighlighted ||
              connections.some(
                (c) =>
                  activeConnIds.has(c.id) && (c.to_id === el.id || c.from_id === el.id)
              ));
          return (
            <ComponentMesh3D
              key={el.id}
              el={el}
              isHighlighted={isHighlighted}
              isPowered={isPowered}
            />
          );
        })}

        {connections.map((conn) => {
          const fromEl = conn.from_id ? byId.get(conn.from_id) : undefined;
          const toEl = conn.to_id ? byId.get(conn.to_id) : undefined;
          const from = fromEl
            ? toWorldPos(fromEl).add(new THREE.Vector3(0, 0.3, 0))
            : toWorldPos({ ...conn, x: conn.x - 8 });
          const to = toEl
            ? toWorldPos(toEl).add(new THREE.Vector3(0, 0.3, 0))
            : toWorldPos({ ...conn, x: conn.x + 8 });
          return (
            <WireMesh3D
              key={conn.id}
              from={from}
              to={to}
              color={conn.color || "#38bdf8"}
              isActive={activeConnIds.has(conn.id)}
            />
          );
        })}
      </Canvas>
      <div className="artifact-3d-hint">Glisser pour pivoter · Molette pour zoomer</div>
    </div>
  );
}

/* ============================================================================
 *  Fenêtre dédiée synchronisée à la voix
 * ========================================================================== */

export function VisualArtifactWindow({ audioEngine }: VisualArtifactWindowProps) {
  const artifact = useJarvisStore((s) => s.activeArtifact);
  const stepIndex = useJarvisStore((s) => s.artifactStepIndex);
  const playing = useJarvisStore((s) => s.artifactPlaying);
  const minimized = useJarvisStore((s) => s.artifactMinimized);
  const viewMode = useJarvisStore((s) => s.artifactViewMode);

  const setStepIndex = useJarvisStore((s) => s.setArtifactStepIndex);
  const setPlaying = useJarvisStore((s) => s.setArtifactPlaying);
  const setMinimized = useJarvisStore((s) => s.setArtifactMinimized);
  const setViewMode = useJarvisStore((s) => s.setArtifactViewMode);
  const closeArtifact = useJarvisStore((s) => s.closeArtifact);

  const [fullscreen, setFullscreen] = useState(false);
  const [speakingStep, setSpeakingStep] = useState(false);
  const webglAvailable = useMemo(() => detectWebGLSupport(), []);

  // Synchronisation Voix <-> Étape visuelle
  useEffect(() => {
    if (!artifact || !playing || artifact.steps.length === 0) {
      setSpeakingStep(false);
      return;
    }
    const step = artifact.steps[stepIndex];
    if (!step) {
      setPlaying(false);
      setSpeakingStep(false);
      return;
    }

    let cancelled = false;
    setSpeakingStep(true);

    void audioEngine
      .speakText(step.spoken_text, apiClient.speakVoice, { fallbackSilentWait: true })
      .then((completed) => {
        if (cancelled) return;
        setSpeakingStep(false);
        if (!completed) {
          setPlaying(false);
          return;
        }
        if (stepIndex < artifact.steps.length - 1) {
          setStepIndex(stepIndex + 1);
        } else {
          setPlaying(false);
        }
      });

    return () => {
      cancelled = true;
      setSpeakingStep(false);
    };
  }, [artifact, stepIndex, playing, audioEngine, setPlaying, setStepIndex]);

  if (!artifact) return null;

  const { visibleIds, highlightedIds, activeConnIds, currentStep } = getCumulativeState(
    artifact,
    stepIndex
  );
  const totalSteps = artifact.steps.length;
  const effectiveMode = viewMode === "3d" && webglAvailable ? "3d" : "2d";

  if (minimized) {
    return (
      <div className="artifact-pill" data-testid="visual-artifact-pill">
        <span className="artifact-pill__dot" aria-hidden="true" />
        <span className="artifact-pill__title">{artifact.title}</span>
        {totalSteps > 0 && (
          <span className="badge mono">
            {stepIndex + 1}/{totalSteps}
          </span>
        )}
        <button
          type="button"
          className="btn btn--sm btn--primary"
          onClick={() => setMinimized(false)}
        >
          Ouvrir
        </button>
        <button
          type="button"
          className="icon-btn icon-btn--sm"
          aria-label="Fermer la fenêtre visuelle"
          onClick={() => {
            audioEngine.stopPlayback();
            closeArtifact();
          }}
        >
          <Icon name="x" size={14} />
        </button>
      </div>
    );
  }

  return (
    <motion.aside
      className={`artifact-window ${fullscreen ? "artifact-window--fullscreen" : ""}`}
      data-testid="visual-artifact-window"
      aria-label={`Fenêtre visuelle : ${artifact.title}`}
      initial={{ opacity: 0, y: 16, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 12 }}
      transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
    >
      <header className="artifact-window__head">
        <div className="artifact-window__meta">
          <span className="badge badge--info">{CATEGORY_LABELS[artifact.category]}</span>
          <div>
            <h2 className="artifact-window__title">{artifact.title}</h2>
            {artifact.subtitle && (
              <p className="artifact-window__sub">{artifact.subtitle}</p>
            )}
          </div>
        </div>

        <div className="artifact-window__actions">
          <div className="artifact-mode-switch" role="group" aria-label="Mode de rendu">
            <button
              type="button"
              className={`artifact-mode-btn ${effectiveMode === "2d" ? "is-active" : ""}`}
              onClick={() => setViewMode("2d")}
            >
              2D Motion
            </button>
            <button
              type="button"
              className={`artifact-mode-btn ${effectiveMode === "3d" ? "is-active" : ""}`}
              disabled={!webglAvailable}
              title={webglAvailable ? "Vue 3D interactive" : "WebGL indisponible"}
              onClick={() => setViewMode("3d")}
            >
              3D
            </button>
          </div>

          <button
            type="button"
            className="icon-btn icon-btn--sm"
            aria-label="Réduire"
            title="Réduire"
            onClick={() => setMinimized(true)}
          >
            <span style={{ fontWeight: 700, lineHeight: 1 }}>−</span>
          </button>
          <button
            type="button"
            className="icon-btn icon-btn--sm"
            aria-label={fullscreen ? "Quitter le plein écran" : "Plein écran"}
            title={fullscreen ? "Quitter le plein écran" : "Plein écran"}
            onClick={() => setFullscreen((v) => !v)}
          >
            <Icon name="maximize" size={14} />
          </button>
          <button
            type="button"
            className="icon-btn icon-btn--sm"
            aria-label="Fermer"
            title="Fermer"
            onClick={() => {
              audioEngine.stopPlayback();
              closeArtifact();
            }}
          >
            <Icon name="x" size={14} />
          </button>
        </div>
      </header>

      <div className="artifact-window__stage">
        {effectiveMode === "3d" ? (
          <Scene3DErrorBoundary
            fallback={
              <Scene2DMotion
                artifact={artifact}
                visibleIds={visibleIds}
                highlightedIds={highlightedIds}
                activeConnIds={activeConnIds}
              />
            }
          >
            <Scene3DInteractive
              artifact={artifact}
              visibleIds={visibleIds}
              highlightedIds={highlightedIds}
              activeConnIds={activeConnIds}
            />
          </Scene3DErrorBoundary>
        ) : (
          <Scene2DMotion
            artifact={artifact}
            visibleIds={visibleIds}
            highlightedIds={highlightedIds}
            activeConnIds={activeConnIds}
          />
        )}

        {currentStep?.annotation && (
          <div className="artifact-annotation">{currentStep.annotation}</div>
        )}
      </div>

      {currentStep && (
        <div className="artifact-window__footer">
          <div className="artifact-step-copy">
            <div className="artifact-step-copy__head">
              <span className="badge mono">
                Étape {stepIndex + 1} / {totalSteps}
              </span>
              <strong>{currentStep.title}</strong>
            </div>
            <p className="artifact-step-copy__spoken">
              {speakingStep ? (
                <span className="text-wave">{currentStep.spoken_text}</span>
              ) : (
                currentStep.spoken_text
              )}
            </p>
          </div>

          <div className="artifact-controls">
            <div className="cluster">
              <button
                type="button"
                className="btn btn--sm"
                disabled={stepIndex <= 0}
                onClick={() => {
                  audioEngine.stopPlayback();
                  setStepIndex(stepIndex - 1);
                }}
              >
                Précédent
              </button>
              <button
                type="button"
                className="btn btn--sm btn--primary"
                onClick={() => {
                  if (playing) {
                    audioEngine.stopPlayback();
                    setPlaying(false);
                  } else {
                    if (stepIndex >= totalSteps - 1) setStepIndex(0);
                    setPlaying(true);
                  }
                }}
              >
                <Icon name={playing ? "pause" : "play"} size={14} />
                {playing ? "Pause" : "Lire"}
              </button>
              <button
                type="button"
                className="btn btn--sm"
                disabled={stepIndex >= totalSteps - 1}
                onClick={() => {
                  audioEngine.stopPlayback();
                  setStepIndex(stepIndex + 1);
                }}
              >
                Suivant
              </button>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                title="Rejouer depuis le début"
                onClick={() => {
                  audioEngine.stopPlayback();
                  setStepIndex(0);
                  setPlaying(true);
                }}
              >
                <Icon name="refresh" size={14} />
              </button>
            </div>

            <div className="artifact-step-dots" role="tablist" aria-label="Étapes du montage">
              {artifact.steps.map((st, idx) => (
                <button
                  key={st.index}
                  type="button"
                  role="tab"
                  aria-selected={idx === stepIndex}
                  className={`artifact-step-dot ${idx === stepIndex ? "is-active" : idx < stepIndex ? "is-done" : ""}`}
                  title={st.title}
                  onClick={() => {
                    audioEngine.stopPlayback();
                    setStepIndex(idx);
                  }}
                >
                  {idx + 1}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </motion.aside>
  );
}
