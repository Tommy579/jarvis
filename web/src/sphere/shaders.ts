export const sphereVertexShader = /* glsl */ `
  uniform float uTime;
  uniform float uAmplitude;
  uniform float uLow;
  uniform float uMid;
  uniform float uHigh;
  uniform float uState;
  uniform float uNoiseScale;
  uniform float uNoiseSpeed;
  uniform vec2 uPointer;

  varying vec3 vNormalWorld;
  varying vec3 vViewPos;
  varying vec3 vLocalPos;
  varying float vDisplacement;

  // Simplex 3D Noise (Ashima Arts)
  vec4 permute(vec4 x) { return mod(((x * 34.0) + 1.0) * x, 289.0); }
  vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }

  float snoise(vec3 v) {
    const vec2 C = vec2(1.0 / 6.0, 1.0 / 3.0);
    const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
    vec3 i  = floor(v + dot(v, C.yyy));
    vec3 x0 = v - i + dot(i, C.xxx);
    vec3 g = step(x0.yzx, x0.xyz);
    vec3 l = 1.0 - g;
    vec3 i1 = min(g.xyz, l.zxy);
    vec3 i2 = max(g.xyz, l.zxy);
    vec3 x1 = x0 - i1 + C.xxx;
    vec3 x2 = x0 - i2 + C.yyy;
    vec3 x3 = x0 - D.yyy;
    i = mod(i, 289.0);
    vec4 p = permute(permute(permute(
              i.z + vec4(0.0, i1.z, i2.z, 1.0))
            + i.y + vec4(0.0, i1.y, i2.y, 1.0))
            + i.x + vec4(0.0, i1.x, i2.x, 1.0));
    float n_ = 1.0 / 7.0;
    vec3 ns = n_ * D.wyz - D.xzx;
    vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
    vec4 x_ = floor(j * ns.z);
    vec4 y_ = floor(j - 7.0 * x_);
    vec4 x = x_ * ns.x + ns.yyyy;
    vec4 y = y_ * ns.x + ns.yyyy;
    vec4 h = 1.0 - abs(x) - abs(y);
    vec4 b0 = vec4(x.xy, y.xy);
    vec4 b1 = vec4(x.zw, y.zw);
    vec4 s0 = floor(b0) * 2.0 + 1.0;
    vec4 s1 = floor(b1) * 2.0 + 1.0;
    vec4 sh = -step(h, vec4(0.0));
    vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
    vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;
    vec3 p0 = vec3(a0.xy, h.x);
    vec3 p1 = vec3(a0.zw, h.y);
    vec3 p2 = vec3(a1.xy, h.z);
    vec3 p3 = vec3(a1.zw, h.w);
    vec4 norm = taylorInvSqrt(vec4(dot(p0, p0), dot(p1, p1), dot(p2, p2), dot(p3, p3)));
    p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
    vec4 m = max(0.6 - vec4(dot(x0, x0), dot(x1, x1), dot(x2, x2), dot(x3, x3)), 0.0);
    m = m * m;
    return 42.0 * dot(m * m, vec4(dot(p0, x0), dot(p1, x1), dot(p2, x2), dot(p3, x3)));
  }

  void main() {
    vLocalPos = position;
    float t = uTime * uNoiseSpeed;
    float nLow = snoise(position * uNoiseScale + vec3(t, t * 0.6, 0.0));
    float nHigh = snoise(position * (uNoiseScale * 2.3) - vec3(0.0, t * 1.4, t * 0.8));

    float thinkingWeight = clamp(1.0 - abs(uState - 2.0), 0.0, 1.0);
    float listeningWeight = clamp(1.0 - abs(uState - 1.0), 0.0, 1.0);
    float speakingWeight = clamp(1.0 - abs(uState - 3.0), 0.0, 1.0);

    float spiral = sin(atan(position.z, position.x) * 3.0 + position.y * 4.5 - uTime * 3.2) * 0.032 * thinkingWeight;
    float breathe = sin(uTime * 1.35) * 0.025;
    float voiceDrive = (nLow * (0.075 + uLow * 0.22) + nHigh * (0.038 + uMid * 0.13 + uHigh * 0.09)) * (0.35 + uAmplitude * 1.4);
    float contract = -0.048 * listeningWeight;

    // Vibration organique sur le contour et la périphérie au rythme du débit de parole
    float rimVibration = speakingWeight * (sin(uTime * 44.0 + position.y * 18.0) * 0.03 + cos(uTime * 36.0 + position.x * 16.0) * 0.025) * (0.55 + uAmplitude * 2.0);

    // Houle et vibration 3D au rythme exact des fréquences de la voix de Jarvis
    float speechWaves = speakingWeight * (
      sin(position.y * 14.0 + uTime * 28.0) * (0.045 + uLow * 0.12) +
      cos(position.x * 16.0 - uTime * 36.0) * (0.035 + uMid * 0.09) +
      sin(atan(position.z, position.x) * 6.0 + uTime * 52.0) * (0.03 + uHigh * 0.08)
    ) * (0.8 + uAmplitude * 2.5);

    float xyLen = length(position.xy);
    vec2 xyDir = xyLen > 0.001 ? position.xy / xyLen : vec2(0.0);
    float pointerPull = dot(xyDir, uPointer) * 0.008 * smoothstep(0.0, 0.4, xyLen);

    float disp = breathe + voiceDrive + spiral + contract + pointerPull + rimVibration + speechWaves;

    vDisplacement = disp;
    vec3 displaced = position + normal * disp;
    vec4 mvPos = modelViewMatrix * vec4(displaced, 1.0);
    vViewPos = -mvPos.xyz;
    vNormalWorld = normalize(normalMatrix * normal);
    gl_Position = projectionMatrix * mvPos;
  }
`;

export const sphereFragmentShader = /* glsl */ `
  uniform float uTime;
  uniform float uAmplitude;
  uniform float uState;
  uniform vec3 uColorA;
  uniform vec3 uColorB;
  uniform vec3 uColorAccent;
  uniform vec2 uPointer;

  varying vec3 vNormalWorld;
  varying vec3 vViewPos;
  varying vec3 vLocalPos;
  varying float vDisplacement;

  void main() {
    vec3 viewDir = normalize(vViewPos);
    vec3 normal = normalize(vNormalWorld);

    float thinkingWeight = clamp(1.0 - abs(uState - 2.0), 0.0, 1.0);
    float speakingWeight = clamp(1.0 - abs(uState - 3.0), 0.0, 1.0);
    float offlineWeight = clamp(1.0 - abs(uState - 5.0), 0.0, 1.0);
    float ndv = max(dot(viewDir, normal), 0.0);
    float fresnel = pow(max(1.0 - ndv, 0.0001), 2.6);

    vec3 lightDir = normalize(vec3(uPointer.x * 0.9 + 0.25, uPointer.y * 0.9 + 0.35, 1.1));
    float diffuse = max(dot(normal, lightDir), 0.0);
    float specular = pow(max(dot(reflect(-lightDir, normal), viewDir), 0.0001), 24.0);

    // Subtle organic energy caustic instead of harsh grid stripes
    float wave = sin(vLocalPos.y * 5.0 + uTime * (1.2 + thinkingWeight * 1.4) + vDisplacement * 14.0) * 0.5 + 0.5;
    float coreMix = clamp(fresnel * 0.78 + diffuse * 0.22 + vDisplacement * 2.1 + 0.14 + uAmplitude * 0.2, 0.0, 1.0);

    float rimPulse = fresnel * (0.48 + uAmplitude * 0.35 + speakingWeight * sin(uTime * 38.0) * 0.14 * (0.4 + uAmplitude));
    float speechPulse = speakingWeight * (sin(uTime * 34.0 + vLocalPos.y * 10.0) * 0.16 * (0.5 + uAmplitude * 1.6));

    vec3 baseColor = mix(uColorA, uColorB, coreMix);
    baseColor += uColorAccent * (wave * 0.08 * (1.0 - offlineWeight));
    baseColor += uColorB * rimPulse * (1.0 - offlineWeight * 0.35);
    baseColor += uColorAccent * (specular * (0.36 + 0.16 * thinkingWeight) * (1.0 - offlineWeight * 0.5) + speechPulse);
    baseColor = clamp(baseColor, 0.0, 1.0);

    gl_FragColor = vec4(baseColor, 0.94);
  }
`;

export const pointVertexShader = /* glsl */ `
  uniform float uPointSize;
  uniform float uPixelRatio;

  void main() {
    vec4 mvPos = modelViewMatrix * vec4(position, 1.0);
    gl_Position = projectionMatrix * mvPos;
    gl_PointSize = uPointSize * uPixelRatio * (4.2 / max(-mvPos.z, 1.0));
  }
`;

export const pointFragmentShader = /* glsl */ `
  uniform vec3 uColor;
  uniform float uOpacity;

  void main() {
    vec2 cxy = 2.0 * gl_PointCoord - 1.0;
    float r2 = dot(cxy, cxy);
    if (r2 > 1.0) discard;
    float alpha = exp(-r2 * 3.6) * uOpacity;
    gl_FragColor = vec4(uColor, alpha);
  }
`;
