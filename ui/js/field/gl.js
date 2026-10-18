// The live field in raw WebGL2: lit density terrain with draped contours, GPU flow
// particles (transform feedback) with a fading trail buffer, glowing people with trails,
// pressure tremor and shock rings, the forecast "future surface", walls and the gate,
// and a restrained bloom on emissive layers. Every effect encodes data:
//   height = density, glow = intensity, motion = velocity, tremor = pressure.

import { mat4, flatGrid, sampleGrid } from "./math.js";
import { tokens, reducedMotion, clamp, easeInOut, isNum } from "../util.js";

const H_PER_DENSITY = 0.22;           // metres of terrain per person/m²
const SUB = 4;                        // terrain mesh subdivisions per cell

// ------------------------------------------------------------------------- GLSL
const SAMPLE = `
uniform highp sampler2D uDen;
uniform highp sampler2D uPress;
uniform highp sampler2D uVel;
uniform vec2 uGrid;
uniform float uCell;
uniform vec2 uArea;
uniform float uHScale;
float tx(highp sampler2D t, int c, int r) {
  c = clamp(c, 0, int(uGrid.x) - 1); r = clamp(r, 0, int(uGrid.y) - 1);
  return texelFetch(t, ivec2(c, r), 0).r;
}
vec4 crw(float t) { float t2 = t*t, t3 = t2*t;
  return vec4(-0.5*t3 + t2 - 0.5*t, 1.5*t3 - 2.5*t2 + 1.0, -1.5*t3 + 2.0*t2 + 0.5*t, 0.5*t3 - 0.5*t2); }
float cubic(highp sampler2D t, vec2 xy) {
  vec2 u = xy / uCell - 0.5; vec2 i = floor(u); vec2 f = u - i;
  vec4 wx = crw(f.x), wy = crw(f.y);
  float s = 0.0;
  for (int dy = -1; dy <= 2; dy++) {
    float row = 0.0;
    for (int dx = -1; dx <= 2; dx++) row += tx(t, int(i.x) + dx, int(i.y) + dy) * wx[dx + 1];
    s += row * wy[dy + 1];
  }
  return s;
}
float bil(highp sampler2D t, vec2 xy) {
  vec2 u = xy / uCell - 0.5; vec2 i = floor(u); vec2 f = clamp(u - i, 0.0, 1.0);
  float a = tx(t, int(i.x), int(i.y)), b = tx(t, int(i.x) + 1, int(i.y));
  float c = tx(t, int(i.x), int(i.y) + 1), d = tx(t, int(i.x) + 1, int(i.y) + 1);
  return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}
float density(vec2 xy) { return max(0.0, cubic(uDen, xy)); }
vec4 vtx(int c, int r) { c = clamp(c, 0, int(uGrid.x) - 1); r = clamp(r, 0, int(uGrid.y) - 1); return texelFetch(uVel, ivec2(c, r), 0); }
vec3 velocity(vec2 xy) {
  vec2 u = xy / uCell - 0.5; vec2 i = floor(u); vec2 f = clamp(u - i, 0.0, 1.0);
  vec4 a = vtx(int(i.x), int(i.y)), b = vtx(int(i.x) + 1, int(i.y)), c = vtx(int(i.x), int(i.y) + 1), d = vtx(int(i.x) + 1, int(i.y) + 1);
  float wa = (1.0-f.x)*(1.0-f.y)*a.z, wb = f.x*(1.0-f.y)*b.z, wc = (1.0-f.x)*f.y*c.z, wd = f.x*f.y*d.z;
  float ws = wa + wb + wc + wd;
  if (ws < 1e-4) return vec3(0.0);
  return vec3((a.xy*wa + b.xy*wb + c.xy*wc + d.xy*wd) / ws, ws);
}
vec3 world(vec2 xy, float h) { return vec3(xy.x - uArea.x * 0.5, h, xy.y - uArea.y * 0.5); }
`;

const BG_VS = `#version 300 es
in vec2 aPos; out vec2 vUV;
void main() { vUV = aPos * 0.5 + 0.5; gl_Position = vec4(aPos, 0.0, 1.0); }`;

const BG_FS = `#version 300 es
precision highp float;
in vec2 vUV; uniform vec3 uStage, uLift, uDeep; uniform vec2 uRes;
layout(location=0) out vec4 o0; layout(location=1) out vec4 o1;
void main() {
  vec2 p = (vUV - vec2(0.6, 0.55)) * vec2(uRes.x / uRes.y, 1.0);
  float r = length(p);
  vec3 c = mix(uLift, uStage, smoothstep(0.0, 0.75, r));
  c = mix(c, uDeep, smoothstep(0.7, 1.4, r));
  o0 = vec4(c, 1.0); o1 = vec4(0.0, 0.0, 0.0, 1.0);
}`;

const FLOOR_VS = `#version 300 es
precision highp float;
in vec2 aXY; uniform mat4 uVP; uniform vec2 uArea; out vec2 vXY;
void main() { vXY = aXY; gl_Position = uVP * vec4(aXY.x - uArea.x*0.5, -0.004, aXY.y - uArea.y*0.5, 1.0); }`;

const FLOOR_FS = `#version 300 es
precision highp float;
in vec2 vXY; uniform vec2 uArea; uniform vec3 uInk, uStage; uniform float uNight, uLineW;
layout(location=0) out vec4 o0; layout(location=1) out vec4 o1;
float grid(vec2 p, float w) { vec2 g = abs(fract(p - 0.5) - 0.5) / max(fwidth(p), vec2(1e-4)); return 1.0 - clamp(min(g.x, g.y) / w - 0.5, 0.0, 1.0); }
void main() {
  vec2 c = clamp(vXY, vec2(0.0), uArea);
  float outside = length(vXY - c);
  float fade = exp(-outside * 0.11);
  float g = grid(vXY, uLineW) * 0.10 + grid(vXY / 5.0, uLineW * 1.2) * 0.10;
  float edge = 1.0 - clamp(min(min(vXY.x, uArea.x - vXY.x), min(vXY.y, uArea.y - vXY.y)) * -1.0, 0.0, 1.0);
  float inside = step(0.0, vXY.x) * step(vXY.x, uArea.x) * step(0.0, vXY.y) * step(vXY.y, uArea.y);
  vec3 col = mix(uStage, uInk, g * fade * (inside > 0.5 ? 1.4 : 1.0));
  if (inside > 0.5) col = mix(col, uInk, 0.035);
  float a = clamp(g * fade * 2.5 + inside * 0.6, 0.0, 1.0);
  o0 = vec4(col, a); o1 = vec4(0.0, 0.0, 0.0, a);
}`;

const TERRAIN_VS = `#version 300 es
precision highp float; precision highp int;
in vec2 aXY;
uniform mat4 uVP; uniform float uTime, uTremor, uLift, uFutureAdd, uDmax;
${SAMPLE}
out vec2 vXY; out float vDen; out vec3 vN; out vec3 vW; out float vAO; out float vP;
void main() {
  float d = density(aXY);
  if (uFutureAdd > 0.0) d += uFutureAdd * clamp(d / max(uDmax, 0.5), 0.0, 1.0);
  float h = d * uHScale;
  const float e = 0.3;
  float dxp = density(aXY + vec2(e, 0.0)), dxm = density(aXY - vec2(e, 0.0));
  float dzp = density(aXY + vec2(0.0, e)), dzm = density(aXY - vec2(0.0, e));
  vec3 n = normalize(vec3(-(dxp - dxm) * uHScale / (2.0 * e), 1.0, -(dzp - dzm) * uHScale / (2.0 * e)));
  float havg = (density(aXY + vec2(1.2, 0.0)) + density(aXY - vec2(1.2, 0.0)) + density(aXY + vec2(0.0, 1.2)) + density(aXY - vec2(0.0, 1.2))) * 0.25;
  vAO = clamp(1.0 + (d - havg) * uHScale * 2.6, 0.6, 1.0);
  float p = bil(uPress, aXY);
  float trem = 0.0;
  if (uTremor > 0.5 && p >= 1.0 && d >= 3.0) {
    float ph = fract(sin(dot(floor(aXY * 3.0), vec2(12.9898, 78.233))) * 43758.5453);
    trem = 0.04 * min(p, 3.0) * (0.6 * sin(6.2831853 * (18.0 * uTime + ph)) + 0.4 * sin(6.2831853 * (11.3 * uTime + ph * 1.7)));
  }
  vec3 w = world(aXY, h + trem + uLift);
  vXY = aXY; vDen = d; vN = n; vW = w; vP = p;
  gl_Position = uVP * vec4(w, 1.0);
}`;

const TERRAIN_FS = `#version 300 es
precision highp float;
in vec2 vXY; in float vDen; in vec3 vN; in vec3 vW; in float vAO; in float vP;
uniform vec3 uLo, uHi, uContour, uEye; uniform vec2 uFocus; uniform float uDim, uNight, uGlow, uAmber, uRed, uLineW, uDmaxScale;
layout(location=0) out vec4 o0; layout(location=1) out vec4 o1;
float iso(float f, float level, float w) { float g = abs(f - level) / max(fwidth(f), 1e-4); return 1.0 - clamp(g / w - 0.5, 0.0, 1.0); }
float isoAll(float f, float w) { float g = abs(fract(f - 0.5) - 0.5) / max(fwidth(f), 1e-4); return 1.0 - clamp(g / w - 0.5, 0.0, 1.0); }
void main() {
  float d = vDen;
  float t = clamp(d / uDmaxScale, 0.0, 1.0);
  vec3 base = mix(uLo, uHi, pow(t, 0.8));
  vec3 L = normalize(vec3(-0.45, 0.82, 0.38));
  float diff = max(dot(vN, L), 0.0);
  vec3 V = normalize(uEye - vW);
  float rim = pow(1.0 - max(dot(vN, V), 0.0), 3.0);
  vec3 col = base * (0.5 + 0.62 * diff) * vAO + uHi * rim * 0.14 * t;
  float fine = isoAll(d, uLineW) * smoothstep(0.55, 1.1, d) * (1.0 - step(0.5, iso(d, uAmber, uLineW * 2.5) + iso(d, uRed, uLineW * 2.5)));
  float idx = max(iso(d, uAmber, uLineW * 2.5), iso(d, uRed, uLineW * 2.5)) * step(0.3, d);
  vec2 gm = abs(fract(vXY - 0.5) - 0.5) / max(fwidth(vXY), vec2(1e-4));
  float datum = 1.0 - clamp(min(gm.x, gm.y) / uLineW - 0.5, 0.0, 1.0);
  col = mix(col, uContour, datum * 0.035);
  col = mix(col, uContour, fine * 0.42);
  col = mix(col, uContour, idx);
  float fd = length(vXY - uFocus);
  col *= mix(1.0, 0.38, uDim * smoothstep(3.0, 10.0, fd));
  o0 = vec4(col, 1.0);
  vec3 emit = uContour * idx * 0.9 + uHi * smoothstep(0.45, 1.0, t) * 0.32 * uNight;
  o1 = vec4(emit * uGlow, 1.0);
}`;

const LINE_VS = `#version 300 es
precision highp float;
in vec4 aV; uniform mat4 uVP; uniform vec2 uArea; out float vA;
void main() { vA = aV.w; gl_Position = uVP * vec4(aV.x - uArea.x * 0.5, aV.y, aV.z - uArea.y * 0.5, 1.0); }`;

const LINE_FS = `#version 300 es
precision highp float;
in float vA; uniform vec3 uColor; uniform float uEmit, uAdditive;
layout(location=0) out vec4 o0; layout(location=1) out vec4 o1;
void main() { o0 = vec4(uColor * vA, vA * (1.0 - uAdditive)); o1 = vec4(uColor * vA * uEmit, 0.0); }`;

const WALL_VS = `#version 300 es
precision highp float;
in vec4 aV; uniform mat4 uVP; uniform vec2 uArea; out float vH; out float vK;
void main() { vH = aV.y; vK = aV.w; gl_Position = uVP * vec4(aV.x - uArea.x * 0.5, aV.y, aV.z - uArea.y * 0.5, 1.0); }`;

const WALL_FS = `#version 300 es
precision highp float;
in float vH; in float vK; uniform vec3 uWall, uGate, uInk; uniform float uGlow, uPulse;
layout(location=0) out vec4 o0; layout(location=1) out vec4 o1;
void main() {
  if (vK > 0.5) {  // gate: glows in the colour of the current advisory
    float a = 0.55 + 0.45 * smoothstep(0.0, 1.2, vH);
    o0 = vec4(uGate * a, 1.0); o1 = vec4(uGate * (0.8 + 0.2 * uPulse) * uGlow, 1.0); return;
  }
  float top = smoothstep(0.27, 0.32, vH);
  vec3 c = mix(uWall, uInk, top * 0.35);
  o0 = vec4(c, 1.0); o1 = vec4(0.0, 0.0, 0.0, 1.0);
}`;

const PEOPLE_VS = `#version 300 es
precision highp float;
in vec2 aCorner; in vec4 aI; in float aSel;
uniform mat4 uVP; uniform vec2 uArea; uniform vec3 uRight, uUp; uniform float uSize;
out vec2 vC; out float vWarm; out float vSel;
void main() {
  vec3 w = vec3(aI.x - uArea.x * 0.5, aI.z + 0.16, aI.y - uArea.y * 0.5);
  float s = uSize * (aSel > 0.5 ? 1.8 : 1.0);
  w += (uRight * aCorner.x + uUp * aCorner.y) * s;
  vC = aCorner; vWarm = aI.w; vSel = aSel;
  gl_Position = uVP * vec4(w, 1.0);
}`;

const PEOPLE_FS = `#version 300 es
precision highp float;
in vec2 vC; in float vWarm; in float vSel;
uniform vec3 uCool, uWarmC; uniform float uAdditive, uGlow;
layout(location=0) out vec4 o0; layout(location=1) out vec4 o1;
void main() {
  float r = length(vC);
  if (r > 1.0) discard;
  float core = smoothstep(0.34, 0.2, r);
  float halo = exp(-r * r * 7.0) * 0.32 * uGlow;
  vec3 c = mix(uCool, uWarmC, vWarm);
  float a = clamp(core + halo, 0.0, 1.0);
  if (uAdditive < 0.5) a = core;
  o0 = vec4(c * a, a * (1.0 - uAdditive));
  o1 = vec4(c * (core * 0.6 + halo) * uGlow + vec3(vSel) * core, 0.0);
}`;

const PART_UPD_VS = `#version 300 es
precision highp float; precision highp int;
in vec4 aP;
uniform float uDt, uTime, uSpeed;
${SAMPLE}
out vec4 vP;
float hash(float n) { return fract(sin(n) * 43758.5453123); }
void main() {
  vec4 p = aP;
  vec3 v = velocity(p.xy);
  p.xy += v.xy * uDt * uSpeed;
  p.z += uDt;
  bool dead = p.z > p.w || p.x < 0.0 || p.y < 0.0 || p.x > uArea.x || p.y > uArea.y || v.z < 0.25;
  if (dead) {
    float seed = float(gl_VertexID) * 1.618 + uTime * 13.37;
    float best = -1.0; vec2 bp = vec2(0.0);
    for (int k = 0; k < 4; k++) {
      vec2 c = vec2(hash(seed + float(k) * 7.1), hash(seed * 1.3 + float(k) * 3.7)) * uArea;
      float d = bil(uDen, c) + hash(seed + float(k)) * 0.4;
      if (d > best) { best = d; bp = c; }
    }
    p = vec4(bp, 0.0, 2.0 + 2.0 * hash(seed * 0.7));
  }
  vP = p;
}`;

const PART_UPD_FS = `#version 300 es
precision highp float; out vec4 o; void main() { o = vec4(0.0); }`;

const PART_VS = `#version 300 es
precision highp float; precision highp int;
in vec4 aP; in float aEnd;
uniform mat4 uVP; uniform float uStreak, uSpeed;
${SAMPLE}
out float vA;
void main() {
  vec3 v = velocity(aP.xy);
  vec2 xy = aP.xy - v.xy * uStreak * uSpeed * aEnd;
  float d = bil(uDen, xy);
  float h = d * uHScale + 0.07;
  gl_Position = uVP * vec4(world(xy, h), 1.0);
  float life = smoothstep(0.0, 0.5, aP.z) * (1.0 - smoothstep(aP.w - 0.8, aP.w, aP.z));
  float valid = smoothstep(0.25, 0.6, v.z);
  vA = life * valid * mix(0.28, 1.0, clamp(d / 5.0, 0.0, 1.0)) * (1.0 - aEnd * 0.9) * clamp(length(v.xy) * 1.6, 0.2, 1.0);
}`;

const PART_FS = `#version 300 es
precision highp float;
in float vA; uniform vec3 uColor; uniform float uIntensity;
out vec4 o;
void main() { float a = vA * uIntensity; o = vec4(uColor * a, a); }`;

const QUAD_VS = `#version 300 es
in vec2 aPos; out vec2 vUV;
void main() { vUV = aPos * 0.5 + 0.5; gl_Position = vec4(aPos, 0.0, 1.0); }`;

const FADE_FS = `#version 300 es
precision highp float;
in vec2 vUV; uniform sampler2D uTex; uniform float uKeep; out vec4 o;
void main() { o = texture(uTex, vUV) * uKeep; }`;

const RING3_VS = `#version 300 es
precision highp float; precision highp int;
in vec2 aCorner; in vec4 aI;
uniform mat4 uVP; uniform float uR;
${SAMPLE}
out vec2 vL; out float vPidx; out float vPhase;
void main() {
  vL = aCorner; vPidx = aI.z; vPhase = aI.w;
  vec2 xy = aI.xy + aCorner * uR;
  float h = density(xy) * uHScale + 0.05;
  gl_Position = uVP * vec4(world(xy, h), 1.0);
}`;

const RING_FS = `#version 300 es
precision highp float;
in vec2 vL; in float vPidx; in float vPhase;
uniform float uTime, uMoving; uniform vec3 uColor;
layout(location=0) out vec4 o0; layout(location=1) out vec4 o1;
void main() {
  float r = length(vL);
  if (r > 1.0) discard;
  float a = 0.0;
  for (int k = 0; k < 2; k++) {
    float rt = uMoving > 0.5 ? fract(uTime / 1.6 + vPhase + float(k) * 0.5) : (0.35 + 0.4 * float(k));
    float w = 0.03;
    a += exp(-pow((r - rt) / w, 2.0)) * (1.0 - rt) * clamp(vPidx / 3.0, 0.35, 0.8) * 0.75;
  }
  o0 = vec4(uColor * a, 0.0); o1 = vec4(uColor * a * 1.2, 0.0);
}`;

const GHOST_FS = `#version 300 es
precision highp float;
in vec2 vXY; in float vDen; in vec3 vN; in vec3 vW; in float vAO; in float vP;
uniform vec3 uColor; uniform float uAlpha;
layout(location=0) out vec4 o0; layout(location=1) out vec4 o1;
void main() {
  float a = uAlpha * smoothstep(1.5, 3.5, vDen);   // only the projected ridge, not the empty floor
  if (a < 0.003) discard;
  o0 = vec4(uColor * a, a); o1 = vec4(uColor * a * 0.6, 0.0);
}`;

const BLUR_FS = `#version 300 es
precision highp float;
in vec2 vUV; uniform sampler2D uTex; uniform vec2 uDir; out vec4 o;
void main() {
  vec3 s = texture(uTex, vUV).rgb * 0.2270270270;
  s += texture(uTex, vUV + uDir * 1.3846153846).rgb * 0.3162162162;
  s += texture(uTex, vUV - uDir * 1.3846153846).rgb * 0.3162162162;
  s += texture(uTex, vUV + uDir * 3.2307692308).rgb * 0.0702702703;
  s += texture(uTex, vUV - uDir * 3.2307692308).rgb * 0.0702702703;
  o = vec4(s, 1.0);
}`;

const FINAL_FS = `#version 300 es
precision highp float;
in vec2 vUV;
uniform sampler2D uColor, uEmit, uBloom, uTrail;
uniform float uBloomK, uNight, uTime, uGrain;
out vec4 o;
vec3 aces(vec3 x) { return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0); }
void main() {
  vec3 c = texture(uColor, vUV).rgb;
  vec4 tr = texture(uTrail, vUV);
  c = c * (1.0 - tr.a * (1.0 - uNight)) + tr.rgb;
  vec3 e = texture(uEmit, vUV).rgb;
  vec3 b = texture(uBloom, vUV).rgb;
  c += (e * 0.25 + b * uBloomK) * uNight;
  if (uNight > 0.5) c = aces(c * 1.08);
  float g = fract(sin(dot(vUV * 1000.0 + uTime, vec2(12.9898, 78.233))) * 43758.5453) - 0.5;
  c += g * uGrain * uNight;
  o = vec4(c, 1.0);
}`;

// ------------------------------------------------------------------------- helpers
function compile(gl, type, src) {
  const s = gl.createShader(type);
  gl.shaderSource(s, src);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
    const msg = gl.getShaderInfoLog(s);
    console.error(msg, src.split("\n").map((l, i) => `${i + 1}: ${l}`).join("\n"));
    throw new Error("shader compile failed: " + msg);
  }
  return s;
}

function program(gl, vs, fs, tfVaryings) {
  const p = gl.createProgram();
  gl.attachShader(p, compile(gl, gl.VERTEX_SHADER, vs));
  gl.attachShader(p, compile(gl, gl.FRAGMENT_SHADER, fs));
  if (tfVaryings) gl.transformFeedbackVaryings(p, tfVaryings, gl.SEPARATE_ATTRIBS);
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error("link failed: " + gl.getProgramInfoLog(p));
  const u = {};
  const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
  for (let i = 0; i < n; i++) { const info = gl.getActiveUniform(p, i); u[info.name] = gl.getUniformLocation(p, info.name); }
  const a = {};
  const na = gl.getProgramParameter(p, gl.ACTIVE_ATTRIBUTES);
  for (let i = 0; i < na; i++) { const info = gl.getActiveAttrib(p, i); a[info.name] = gl.getAttribLocation(p, info.name); }
  return { p, u, a };
}

function floatTex(gl, w, h, internal, format, data) {
  const t = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, t);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texImage2D(gl.TEXTURE_2D, 0, internal, w, h, 0, format, gl.FLOAT, data);
  return t;
}

function colorTex(gl, w, h) {
  const t = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, t);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
  return t;
}

function fboFor(gl, textures) {
  const f = gl.createFramebuffer();
  gl.bindFramebuffer(gl.FRAMEBUFFER, f);
  textures.forEach((t, i) => gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0 + i, gl.TEXTURE_2D, t, 0));
  return f;
}

// ------------------------------------------------------------------------- renderer
export class FieldGL {
  static supported() {
    try { const c = document.createElement("canvas"); return !!c.getContext("webgl2"); } catch (e) { return false; }
  }

  constructor(canvas, labelsEl, opts = {}) {
    this.canvas = canvas;
    this.labelsEl = labelsEl;
    this.opts = opts;
    const gl = canvas.getContext("webgl2", { antialias: false, alpha: false, premultipliedAlpha: false, preserveDrawingBuffer: false, powerPreference: "high-performance" });
    if (!gl) throw new Error("WebGL2 unavailable");
    this.gl = gl;
    this.kind = "webgl2";
    this.samples = Math.min(4, gl.getParameter(gl.MAX_SAMPLES) || 1);
    this.timerExt = gl.getExtension("EXT_disjoint_timer_query_webgl2");
    this.gpuMs = null; this.cpuMs = 0; this.slowSince = null; this.degrade = 0;
    this.lowPower = false;
    this.nParticles = 8000;
    this.area = null; this.grid = null; this.state = null;
    this.denCur = null; this.denFrom = null; this.denTo = null; this.blendStart = 0;
    this.dmaxScale = 8;
    this.tracks = new Map();     // id -> [{x,y,w}]
    this.peopleCount = 0;
    this.selected = null;
    this.rings = [];
    this.labels = new Map();
    this.cam = { yaw: -0.35, pitch: 0.61, dist: 30, tx: 0, tz: 0 };
    this.camTween = null;
    this.focus = { x: 0, y: 0, dim: 0, dimTarget: 0 };
    this.future = null;
    this.contours = {};
    this.thresholds = { amber: 4, red: 6 };
    this.gateLevel = "GREEN";
    this.tok = tokens();
    this.t0 = performance.now();
    this.lastFrame = this.t0;
    this.cameraMovedAt = 0;

    this.progs = {
      bg: program(gl, BG_VS, BG_FS), floor: program(gl, FLOOR_VS, FLOOR_FS),
      terrain: program(gl, TERRAIN_VS, TERRAIN_FS), ghost: program(gl, TERRAIN_VS, GHOST_FS),
      line: program(gl, LINE_VS, LINE_FS), wall: program(gl, WALL_VS, WALL_FS),
      people: program(gl, PEOPLE_VS, PEOPLE_FS), pupd: program(gl, PART_UPD_VS, PART_UPD_FS, ["vP"]),
      part: program(gl, PART_VS, PART_FS), fade: program(gl, QUAD_VS, FADE_FS), ring: program(gl, RING3_VS, RING_FS),
      blur: program(gl, QUAD_VS, BLUR_FS), final: program(gl, QUAD_VS, FINAL_FS),
    };
    this.quad = this._buffer(new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]));
    this.corner = this._buffer(new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]));
    this.endsBuf = this._buffer(new Float32Array([0, 1]));
    this.dataTex = { den: null, press: null, vel: null, w: 0, h: 0 };
    this._initWorker();
    this._bindInput();
    this.resize();
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(canvas);
    this.intro = reducedMotion() ? null : { start: performance.now(), dur: 2500 };
  }

  _buffer(data, usage) {
    const gl = this.gl, b = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, b);
    gl.bufferData(gl.ARRAY_BUFFER, data, usage || gl.STATIC_DRAW);
    return b;
  }

  _initWorker() {
    try {
      this.worker = new Worker(new URL("./contours.worker.js", import.meta.url), { type: "module" });
      this.worker.onmessage = (ev) => { this.contours = ev.data.out; };
    } catch (e) { this.worker = null; }
  }

  // ---------------------------------------------------------------- size / targets
  resize() {
    const gl = this.gl, dpr = Math.min(window.devicePixelRatio || 1, this.lowPower ? 1 : 2);
    const w = Math.max(2, Math.round(this.canvas.clientWidth * dpr)), h = Math.max(2, Math.round(this.canvas.clientHeight * dpr));
    if (w === this.W && h === this.H) return;
    this.W = w; this.H = h; this.canvas.width = w; this.canvas.height = h;
    // MSAA scene target with two colour attachments (colour + emissive)
    for (const k of ["msFbo", "resolveFbo", "trailFboA", "trailFboB", "bloomFboA", "bloomFboB"]) if (this[k]) gl.deleteFramebuffer(this[k]);
    for (const k of ["rbColor", "rbEmit", "rbDepth"]) if (this[k]) gl.deleteRenderbuffer(this[k]);
    this.msFbo = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.msFbo);
    const rb = (fmt, att) => { const r = gl.createRenderbuffer(); gl.bindRenderbuffer(gl.RENDERBUFFER, r);
      gl.renderbufferStorageMultisample(gl.RENDERBUFFER, this.samples, fmt, w, h); gl.framebufferRenderbuffer(gl.FRAMEBUFFER, att, gl.RENDERBUFFER, r); return r; };
    this.rbColor = rb(gl.RGBA8, gl.COLOR_ATTACHMENT0);
    this.rbEmit = rb(gl.RGBA8, gl.COLOR_ATTACHMENT1);
    this.rbDepth = rb(gl.DEPTH_COMPONENT24, gl.DEPTH_ATTACHMENT);
    gl.drawBuffers([gl.COLOR_ATTACHMENT0, gl.COLOR_ATTACHMENT1]);
    this.texColor = colorTex(gl, w, h); this.texEmit = colorTex(gl, w, h);
    this.resolveFbo = fboFor(gl, [this.texColor, this.texEmit]);
    this.texTrailA = colorTex(gl, w, h); this.texTrailB = colorTex(gl, w, h);
    this.trailFboA = fboFor(gl, [this.texTrailA]); this.trailFboB = fboFor(gl, [this.texTrailB]);
    for (const f of [this.trailFboA, this.trailFboB]) { gl.bindFramebuffer(gl.FRAMEBUFFER, f); gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT); }
    const bw = Math.max(2, w >> 2), bh = Math.max(2, h >> 2);
    this.bw = bw; this.bh = bh;
    this.texBloomA = colorTex(gl, bw, bh); this.texBloomB = colorTex(gl, bw, bh);
    this.bloomFboA = fboFor(gl, [this.texBloomA]); this.bloomFboB = fboFor(gl, [this.texBloomB]);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    this.cameraMovedAt = performance.now();
  }

  // ---------------------------------------------------------------- data
  setState(s) {
    if (!s || !s.grid || !s.area) return;
    const gl = this.gl, g = s.grid, rows = g.rows, cols = g.cols;
    const areaChanged = !this.area || this.area.w !== s.area.width_m || this.area.h !== s.area.height_m || !this.grid || this.grid.rows !== rows || this.grid.cols !== cols;
    this.area = { w: s.area.width_m, h: s.area.height_m };
    this.grid = { rows, cols, cell: g.cell_m || 1 };
    this.state = s;
    this.thresholds = { amber: s.thresholds?.amber_density ?? 4, red: s.thresholds?.red_density ?? 6 };
    this.dmaxScale = Math.max(8, this.thresholds.red * 1.33);
    const den = flatGrid(g.density, rows, cols, 0);
    const pthr = s.pressure?.threshold || s.thresholds?.p_thr || 0.02;
    const press = flatGrid(g.pressure, rows, cols, 0).map((v) => v / pthr);
    let vx = g.vx, vy = g.vy;
    const vel = new Float32Array(rows * cols * 4);
    if (vx && vy) {
      for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
        const a = vx[r]?.[c], b = vy[r]?.[c], k = (r * cols + c) * 4;
        if (isNum(a) && isNum(b)) { vel[k] = a; vel[k + 1] = b; vel[k + 2] = 1; }
      }
    } else if (s.tracks) {
      this._velFromTracks(s.tracks, vel, rows, cols, this.grid.cell);
    }
    if (areaChanged) {
      this._buildMesh();
      this._buildWalls(s.gate);
      this.denCur = den.slice();
      if (!this.camUser) this.resetView(true);
      for (const k of ["den", "press", "vel"]) if (this.dataTex[k]) gl.deleteTexture(this.dataTex[k]);
      this.dataTex.den = floatTex(gl, cols, rows, gl.R32F, gl.RED, this.denCur);
      this.dataTex.press = floatTex(gl, cols, rows, gl.R32F, gl.RED, press);
      this.dataTex.vel = floatTex(gl, cols, rows, gl.RGBA32F, gl.RGBA, vel);
      this._initParticles(this.lowPower ? 2000 : this.nParticles);
    } else {
      gl.bindTexture(gl.TEXTURE_2D, this.dataTex.press);
      gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, cols, rows, gl.RED, gl.FLOAT, press);
      gl.bindTexture(gl.TEXTURE_2D, this.dataTex.vel);
      gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, cols, rows, gl.RGBA, gl.FLOAT, vel);
    }
    if (s.gate && JSON.stringify(s.gate) !== JSON.stringify(this.gate)) this._buildWalls(s.gate);
    this.denFrom = this.denCur ? this.denCur.slice() : den.slice();
    this.denTo = den;
    this.blendStart = performance.now();
    this.pressGrid = press;
    this.gateLevel = s.status?.level || "GREEN";
    this._updateTracks(s);
    this._updateRings(press, den);
    if (this.worker) this.worker.postMessage({ id: s.seq, data: den, rows, cols, cellM: this.grid.cell, levels: [this.thresholds.amber, this.thresholds.red], k: 4 });
  }

  _velFromTracks(tracks, vel, rows, cols, cell) {
    const R2 = 1, cut2 = 9;
    const sx = new Float32Array(rows * cols), sy = new Float32Array(rows * cols), sw = new Float32Array(rows * cols);
    for (const t of tracks) {
      if (!isNum(t.vx) || !isNum(t.vy)) continue;
      for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
        const dx = t.x - (c + 0.5) * cell, dy = t.y - (r + 0.5) * cell, d2 = dx * dx + dy * dy;
        if (d2 > cut2) continue;
        const w = Math.exp(-d2 / R2), k = r * cols + c;
        sx[k] += w * t.vx; sy[k] += w * t.vy; sw[k] += w;
      }
    }
    for (let k = 0; k < rows * cols; k++) if (sw[k] >= 1) { vel[k * 4] = sx[k] / sw[k]; vel[k * 4 + 1] = sy[k] / sw[k]; vel[k * 4 + 2] = 1; }
  }

  _updateTracks(s) {
    const now = performance.now() / 1000;
    const seen = new Set();
    for (const t of s.tracks || []) {
      if (t.id === null || t.id === undefined) continue;
      seen.add(t.id);
      let h = this.tracks.get(t.id);
      if (!h) { h = []; this.tracks.set(t.id, h); }
      h.push({ x: t.x, y: t.y, w: now, vx: t.vx, vy: t.vy });
      while (h.length && now - h[0].w > 3.0) h.shift();
    }
    for (const [id, h] of this.tracks) {
      if (!seen.has(id)) { while (h.length && now - h[0].w > 3.0) h.shift(); if (!h.length || now - h[h.length - 1].w > 1.0) this.tracks.delete(id); }
    }
    this.trackList = (s.tracks || []).filter((t) => isNum(t.x) && isNum(t.y));
  }

  _updateRings(press, den) {
    const { rows, cols, cell } = this.grid;
    const hot = [];
    for (let k = 0; k < rows * cols; k++) if (press[k] >= 1 && den[k] >= 3) hot.push(k);
    hot.sort((a, b) => press[b] - press[a]);
    const rings = [];
    for (const k of hot) {
      const r = Math.floor(k / cols), c = k % cols, x = (c + 0.5) * cell, y = (r + 0.5) * cell;
      if (rings.some((q) => Math.hypot(q.x - x, q.y - y) < 3.2)) continue;
      rings.push({ x, y, p: press[k], phase: ((r * 7 + c * 3) % 10) / 10 });
      if (rings.length >= 6) break;
    }
    this.rings = rings;
  }

  _buildMesh() {
    const gl = this.gl, { rows, cols, cell } = this.grid;
    const sub = this.lowPower ? 2 : SUB;
    const nx = cols * sub + 1, ny = rows * sub + 1;
    const v = new Float32Array(nx * ny * 2);
    for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) { const k = (j * nx + i) * 2; v[k] = (i / sub) * cell; v[k + 1] = (j / sub) * cell; }
    const idx = new Uint32Array((nx - 1) * (ny - 1) * 6);
    let p = 0;
    for (let j = 0; j < ny - 1; j++) for (let i = 0; i < nx - 1; i++) {
      const a = j * nx + i, b = a + 1, c = a + nx, d = c + 1;
      idx[p++] = a; idx[p++] = c; idx[p++] = b; idx[p++] = b; idx[p++] = c; idx[p++] = d;
    }
    const lines = [];
    for (let j = 0; j < ny; j += 2) for (let i = 0; i < nx - 1; i++) lines.push(j * nx + i, j * nx + i + 1);
    for (let i = 0; i < nx; i += 2) for (let j = 0; j < ny - 1; j++) lines.push(j * nx + i, (j + 1) * nx + i);
    if (this.mesh) { gl.deleteBuffer(this.mesh.vb); gl.deleteBuffer(this.mesh.ib); gl.deleteBuffer(this.mesh.lb); }
    const vb = this._buffer(v);
    const ib = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ib); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW);
    const lb = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, lb); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint32Array(lines), gl.STATIC_DRAW);
    this.mesh = { vb, ib, lb, n: idx.length, nl: lines.length };
    // floor plane (area +/- 40 m)
    const W = this.area.w, H = this.area.h, m = 40;
    this.floorBuf = this._buffer(new Float32Array([-m, -m, W + m, -m, -m, H + m, -m, H + m, W + m, -m, W + m, H + m]));
  }

  _buildWalls(gate) {
    this.gate = gate ? { ...gate } : null;
    const W = this.area.w, H = this.area.h, h = 0.32, t = [];
    const quad = (x0, y0, x1, y1, height, kind) => {
      t.push(x0, 0, y0, kind, x1, 0, y1, kind, x0, height, y0, kind, x0, height, y0, kind, x1, 0, y1, kind, x1, height, y1, kind);
    };
    quad(0, 0, W, 0, h, 0);
    quad(0, H, W, H, h, 0);
    // the gate sits on the x = 0 or x = W wall between y0 and y1; it glows in the advisory colour
    const g = gate && isNum(gate.y0) && isNum(gate.y1) ? gate : null;
    for (const wx of [0, W]) {
      if (g && Math.abs((g.x ?? W) - wx) < 0.01) {
        quad(wx, 0, wx, g.y0, h, 0); quad(wx, g.y1, wx, H, h, 0);
        quad(wx, g.y0, wx, g.y1, 1.4, 1);
      } else quad(wx, 0, wx, H, h, 0);
    }
    if (this.wallBuf) this.gl.deleteBuffer(this.wallBuf);
    this.wallBuf = this._buffer(new Float32Array(t));
    this.wallN = t.length / 4;
  }

  _initParticles(n) {
    const gl = this.gl;
    this.nParticles = n;
    const d = new Float32Array(n * 4);
    for (let i = 0; i < n; i++) { d[i * 4] = Math.random() * (this.area?.w || 1); d[i * 4 + 1] = Math.random() * (this.area?.h || 1); d[i * 4 + 2] = 99; d[i * 4 + 3] = 1; }
    for (const b of this.pBufs || []) gl.deleteBuffer(b);
    for (const v of [...(this.vaoUpd || []), ...(this.vaoDraw || [])]) gl.deleteVertexArray(v);
    this.pBufs = [this._buffer(d, gl.DYNAMIC_COPY), this._buffer(d, gl.DYNAMIC_COPY)];
    // one VAO per buffer and purpose, so transform feedback never reads and writes one binding
    const pu = this.progs.pupd, pr = this.progs.part;
    this.vaoUpd = this.pBufs.map((b) => { const v = gl.createVertexArray(); gl.bindVertexArray(v); this._attrib(pu, "aP", b, 4); return v; });
    this.vaoDraw = this.pBufs.map((b) => { const v = gl.createVertexArray(); gl.bindVertexArray(v); this._attrib(pr, "aP", b, 4, 1); this._attrib(pr, "aEnd", this.endsBuf, 1, 0); return v; });
    gl.bindVertexArray(null);
    gl.bindBuffer(gl.ARRAY_BUFFER, null);
    this.pIdx = 0;
  }

  // ---------------------------------------------------------------- camera
  defaultCam() {
    const W = this.area?.w || 24, H = this.area?.h || 12, aspect = this.W / this.H || 1.6;
    const fov = 0.62, span = Math.max(W / Math.min(aspect, 2.2), H * 1.25);
    // look from the real camera's side (from the calibration) with a slight turn for depth
    const yaw = isNum(this.viewYaw) ? this.viewYaw - 0.3 : -0.35;
    return { yaw, pitch: 0.66, dist: (span * 0.47) / Math.tan(fov / 2) + 1, tx: 0, tz: 0.6 * Math.cos(yaw) };
  }

  resetView(animate) {
    const to = this.defaultCam();
    this.camUser = false;
    if (animate === true && this.intro) {
      this.intro = null;
      const from = { ...to, pitch: 1.35, dist: to.dist * 1.9, yaw: to.yaw - 0.6 };
      this.cam = { ...from };
      this.camTween = { from, to, start: performance.now(), dur: 2500 };
    } else if (animate && !reducedMotion()) {
      this.camTween = { from: { ...this.cam }, to, start: performance.now(), dur: 800 };
    } else { this.cam = { ...to }; this.camTween = null; }
    this.cameraMovedAt = performance.now();
  }

  focusOn(x, y, durMs = 1200) {
    if (!this.area) return;
    const base = this.defaultCam();
    const to = { ...base, dist: base.dist * 0.62, tx: x - this.area.w / 2, tz: y - this.area.h / 2, pitch: 0.72 };
    if (reducedMotion()) { this.cam = { ...to }; return; }
    this.camTween = { from: { ...this.cam }, to, start: performance.now(), dur: durMs };
  }

  setDim(on, x, y) {
    this.focus.dimTarget = on ? 1 : 0;
    if (isNum(x)) { this.focus.x = x; this.focus.y = y; }
  }

  setFuture(f) { this.future = f; }   // {add, dmax, label} or null

  // yaw (radians) of the direction from the floor centre toward the real camera
  setViewHint(yaw) { this.viewYaw = yaw; if (this.area && !this.camUser) this.resetView("smooth"); }

  setLowPower(on) {
    this.lowPower = on;
    if (this.area) { this._buildMesh(); this._initParticles(on ? 2000 : 8000); }
    this.W = 0; this.resize();
  }

  refreshTheme() { this.tok = tokens(); }

  _camMatrices() {
    let c = this.cam;
    const now = performance.now();
    if (this.camTween) {
      const tw = this.camTween, u = Math.min(1, (now - tw.start) / tw.dur), e = easeInOut(u);
      c = this.cam = {};
      for (const k of ["yaw", "pitch", "dist", "tx", "tz"]) c[k] = tw.from[k] + (tw.to[k] - tw.from[k]) * e;
      if (u >= 1) this.camTween = null;
      this.cameraMovedAt = now;
    }
    const cp = Math.cos(c.pitch), eye = [c.tx + c.dist * cp * Math.sin(c.yaw), c.dist * Math.sin(c.pitch), c.tz + c.dist * cp * Math.cos(c.yaw)];
    const target = [c.tx, 0.6, c.tz];
    const view = mat4.lookAt(eye, target, [0, 1, 0]);
    const proj = mat4.perspective(0.62, this.W / this.H, 0.1, 400);
    this.vp = mat4.multiply(proj, view);
    this.eye = eye;
    this.right = [view[0], view[4], view[8]];
    this.up = [view[1], view[5], view[9]];
    return this.vp;
  }

  // Project a floor point (metres) at height h to CSS pixels.
  project(x, y, h = 0) {
    if (!this.vp || !this.area) return null;
    const p = mat4.transform(this.vp, x - this.area.w / 2, h, y - this.area.h / 2);
    if (p[3] <= 0) return null;
    const cw = this.canvas.clientWidth, ch = this.canvas.clientHeight;
    return [(p[0] * 0.5 + 0.5) * cw, (1 - (p[1] * 0.5 + 0.5)) * ch, p[2]];
  }

  // Screen (CSS px) -> floor (metres) by intersecting the view ray with the floor plane.
  unproject(sx, sy) {
    if (!this.vp || !this.area) return null;
    const inv = mat4.invert(this.vp), cw = this.canvas.clientWidth, ch = this.canvas.clientHeight;
    const nx = (sx / cw) * 2 - 1, ny = 1 - (sy / ch) * 2;
    const a = mat4.transform(inv, nx, ny, -1), b = mat4.transform(inv, nx, ny, 1);
    const dy = b[1] - a[1];
    if (Math.abs(dy) < 1e-9) return null;
    const t = -a[1] / dy;
    return [a[0] + (b[0] - a[0]) * t + this.area.w / 2, a[2] + (b[2] - a[2]) * t + this.area.h / 2];
  }

  densityAt(x, y) {
    if (!this.denCur || !this.grid) return 0;
    return Math.max(0, sampleGrid(this.denCur, this.grid.rows, this.grid.cols, this.grid.cell, x, y));
  }

  pick(sx, sy) {
    const p = this.unproject(sx, sy);
    if (!p || !this.trackList) return null;
    let best = null, bd = 0.9;
    for (const t of this.trackList) { const d = Math.hypot(t.x - p[0], t.y - p[1]); if (d < bd) { bd = d; best = t; } }
    return best;
  }

  // ---------------------------------------------------------------- input
  _bindInput() {
    const c = this.canvas, ptrs = new Map();
    let last = null, pinch = null;
    c.addEventListener("pointerdown", (e) => { c.setPointerCapture(e.pointerId); ptrs.set(e.pointerId, [e.clientX, e.clientY]); last = [e.clientX, e.clientY]; });
    c.addEventListener("pointerup", (e) => { ptrs.delete(e.pointerId); pinch = null; last = null; });
    c.addEventListener("pointercancel", (e) => { ptrs.delete(e.pointerId); pinch = null; last = null; });
    c.addEventListener("pointermove", (e) => {
      if (ptrs.has(e.pointerId)) ptrs.set(e.pointerId, [e.clientX, e.clientY]);
      if (ptrs.size === 2) {
        const [a, b] = [...ptrs.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        if (pinch) { this.cam.dist = clamp(this.cam.dist * (pinch / d), 4, 220); this._userMoved(); }
        pinch = d; return;
      }
      if (last && ptrs.size === 1) {
        const dx = e.clientX - last[0], dy = e.clientY - last[1];
        last = [e.clientX, e.clientY];
        this.cam.yaw -= dx * 0.006;
        this.cam.pitch = clamp(this.cam.pitch + dy * 0.005, 0.18, 1.5);
        this._userMoved();
        return;
      }
      const r = c.getBoundingClientRect();
      this.hover = [e.clientX - r.left, e.clientY - r.top];
    });
    c.addEventListener("pointerleave", () => { this.hover = null; });
    c.addEventListener("wheel", (e) => { e.preventDefault(); this.cam.dist = clamp(this.cam.dist * Math.exp(e.deltaY * 0.0012), 4, 220); this._userMoved(); }, { passive: false });
    c.addEventListener("dblclick", () => this.resetView("smooth"));
  }

  _userMoved() { this.camTween = null; this.camUser = true; this.cameraMovedAt = performance.now(); }

  // ---------------------------------------------------------------- frame
  frame(now) {
    const gl = this.gl;
    const t0 = performance.now();
    if (this.lowPower && now - this.lastFrame < 1000 / 15 - 2) return;
    const dt = Math.min(0.1, (now - this.lastFrame) / 1000);
    this.lastFrame = now;
    this.resize();
    const tok = this.tok, time = (now - this.t0) / 1000, motion = !reducedMotion();
    const vp = this._camMatrices();
    // blend density toward the latest grid (200 ms ease) so the surface breathes
    if (this.denTo && this.denFrom) {
      const u = motion ? Math.min(1, (now - this.blendStart) / 200) : 1, e = easeInOut(u);
      const n = this.denTo.length;
      if (!this.denCur || this.denCur.length !== n) this.denCur = new Float32Array(n);
      for (let k = 0; k < n; k++) this.denCur[k] = this.denFrom[k] + (this.denTo[k] - this.denFrom[k]) * e;
      gl.bindTexture(gl.TEXTURE_2D, this.dataTex.den);
      gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, this.grid.cols, this.grid.rows, gl.RED, gl.FLOAT, this.denCur);
    }
    this.focus.dim += (this.focus.dimTarget - this.focus.dim) * Math.min(1, dt * 4);

    let q = null;
    if (this.timerExt && !this.pendingQuery) { q = gl.createQuery(); gl.beginQuery(this.timerExt.TIME_ELAPSED_EXT, q); }

    // --- scene into the MSAA target
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.msFbo);
    gl.drawBuffers([gl.COLOR_ATTACHMENT0, gl.COLOR_ATTACHMENT1]);
    gl.viewport(0, 0, this.W, this.H);
    gl.clearColor(0, 0, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.disable(gl.DEPTH_TEST); gl.disable(gl.BLEND);
    this._drawQuad(this.progs.bg, (u) => { gl.uniform3fv(u.uStage, tok.stage); gl.uniform3fv(u.uLift, tok.stageLift); gl.uniform3fv(u.uDeep, tok.stageDeep); gl.uniform2f(u.uRes, this.W, this.H); });
    if (this.area && this.mesh) {
      const A = [this.area.w, this.area.h];
      // floor grid
      gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      this._use(this.progs.floor, (u) => { gl.uniformMatrix4fv(u.uVP, false, vp); gl.uniform2fv(u.uArea, A); gl.uniform3fv(u.uInk, tok.ink); gl.uniform3fv(u.uStage, tok.stage); gl.uniform1f(u.uNight, tok.night ? 1 : 0); gl.uniform1f(u.uLineW, tok.line); });
      this._attrib(this.progs.floor, "aXY", this.floorBuf, 2);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
      gl.disable(gl.BLEND);
      // terrain
      gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LEQUAL); gl.depthMask(true);
      this._drawTerrain(this.progs.terrain, vp, time, motion, 0, (u) => {
        gl.uniform3fv(u.uLo, tok.terrainLo); gl.uniform3fv(u.uHi, tok.terrainHi); gl.uniform3fv(u.uContour, tok.contour);
        gl.uniform3fv(u.uEye, this.eye); gl.uniform2f(u.uFocus, this.focus.x, this.focus.y); gl.uniform1f(u.uDim, this.focus.dim);
        gl.uniform1f(u.uNight, tok.night ? 1 : 0); gl.uniform1f(u.uGlow, tok.glow); gl.uniform1f(u.uAmber, this.thresholds.amber);
        gl.uniform1f(u.uRed, this.thresholds.red); gl.uniform1f(u.uLineW, tok.line); gl.uniform1f(u.uDmaxScale, this.dmaxScale);
      });
      gl.drawElements(gl.TRIANGLES, this.mesh.n, gl.UNSIGNED_INT, 0);
      // walls and the gate
      const gateCol = this.gateLevel === "RED" ? tok.crit : this.gateLevel === "AMBER" ? tok.warn : tok.ok;
      this._use(this.progs.wall, (u) => { gl.uniformMatrix4fv(u.uVP, false, vp); gl.uniform2fv(u.uArea, A);
        gl.uniform3fv(u.uWall, tok.night ? [0.10, 0.15, 0.24] : [0.72, 0.78, 0.75]); gl.uniform3fv(u.uGate, gateCol); gl.uniform3fv(u.uInk, tok.ink);
        gl.uniform1f(u.uGlow, tok.glow); gl.uniform1f(u.uPulse, this.gateLevel === "RED" && motion ? 0.5 + 0.5 * Math.sin(time * 4) : 0.5); });
      this._attrib(this.progs.wall, "aV", this.wallBuf, 4);
      gl.drawArrays(gl.TRIANGLES, 0, this.wallN);
      // forecast horizon: ghost of the projected surface
      if (this.future && this.future.add > 0) {
        gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA); gl.depthMask(false);
        this._drawTerrain(this.progs.ghost, vp, time, false, this.future.add, (u) => {
          gl.uniform3fv(u.uColor, tok.ink); gl.uniform1f(u.uAlpha, tok.night ? 0.28 : 0.4); });
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.mesh.lb);
        gl.drawElements(gl.LINES, this.mesh.nl, gl.UNSIGNED_INT, 0);
        gl.depthMask(true);
      }
      // people trails + people
      gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA); gl.depthMask(false);
      this._drawTrails(vp, tok, A);
      this._drawPeople(vp, tok, A);
      // shock rings
      if (this.rings.length) this._drawRings(vp, tok, time, motion);
      gl.depthMask(true); gl.disable(gl.BLEND); gl.disable(gl.DEPTH_TEST);
    }

    // --- resolve MSAA -> textures
    gl.bindFramebuffer(gl.READ_FRAMEBUFFER, this.msFbo);
    gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, this.resolveFbo);
    gl.readBuffer(gl.COLOR_ATTACHMENT0); gl.drawBuffers([gl.COLOR_ATTACHMENT0, gl.NONE]);
    gl.blitFramebuffer(0, 0, this.W, this.H, 0, 0, this.W, this.H, gl.COLOR_BUFFER_BIT, gl.NEAREST);
    gl.readBuffer(gl.COLOR_ATTACHMENT1); gl.drawBuffers([gl.NONE, gl.COLOR_ATTACHMENT1]);
    gl.blitFramebuffer(0, 0, this.W, this.H, 0, 0, this.W, this.H, gl.COLOR_BUFFER_BIT, gl.NEAREST);
    gl.bindFramebuffer(gl.READ_FRAMEBUFFER, null); gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, null);

    // --- particles: update (transform feedback) and draw into the decaying trail buffer
    if (this.area && this.pBufs) this._particles(vp, tok, dt, time, motion, now);

    // --- bloom on emissive layers
    const bloom = tok.night && this.degrade < 2;
    if (bloom) {
      gl.viewport(0, 0, this.bw, this.bh);
      gl.bindFramebuffer(gl.FRAMEBUFFER, this.bloomFboA);
      this._drawQuad(this.progs.blur, (u) => { this._tex(0, this.texEmit, u.uTex); gl.uniform2f(u.uDir, 1 / this.bw, 0); });
      for (let i = 0; i < 2; i++) {
        gl.bindFramebuffer(gl.FRAMEBUFFER, this.bloomFboB);
        this._drawQuad(this.progs.blur, (u) => { this._tex(0, this.texBloomA, u.uTex); gl.uniform2f(u.uDir, 0, 1 / this.bh * (1 + i)); });
        gl.bindFramebuffer(gl.FRAMEBUFFER, this.bloomFboA);
        this._drawQuad(this.progs.blur, (u) => { this._tex(0, this.texBloomB, u.uTex); gl.uniform2f(u.uDir, 1 / this.bw * (1 + i), 0); });
      }
    }
    // --- composite to the canvas
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.viewport(0, 0, this.W, this.H);
    this._drawQuad(this.progs.final, (u) => {
      this._tex(0, this.texColor, u.uColor); this._tex(1, this.texEmit, u.uEmit); this._tex(2, this.texBloomA, u.uBloom); this._tex(3, this.trailTex || this.texTrailA, u.uTrail);
      gl.uniform1f(u.uBloomK, bloom ? 1.1 : 0); gl.uniform1f(u.uNight, tok.night ? 1 : 0); gl.uniform1f(u.uTime, motion ? time : 0); gl.uniform1f(u.uGrain, motion ? 0.025 : 0);
    });

    if (q) { gl.endQuery(this.timerExt.TIME_ELAPSED_EXT); this.pendingQuery = q; }
    if (this.pendingQuery && this.pendingQuery !== q) {
      const pq = this.pendingQuery;
      if (gl.getQueryParameter(pq, gl.QUERY_RESULT_AVAILABLE)) {
        if (!gl.getParameter(this.timerExt.GPU_DISJOINT_EXT)) this.gpuMs = gl.getQueryParameter(pq, gl.QUERY_RESULT) / 1e6;
        gl.deleteQuery(pq); this.pendingQuery = null;
      }
    }
    this._labels(now, time);
    this.cpuMs = this.cpuMs * 0.9 + (performance.now() - t0) * 0.1;
    this._autoDegrade(now);
  }

  _autoDegrade(now) {
    const ms = Math.max(this.cpuMs, this.gpuMs || 0);
    if (ms > 14) {
      if (this.slowSince === null) this.slowSince = now;
      else if (now - this.slowSince > 2000 && this.degrade < 2) {
        this.degrade++; this.slowSince = null;
        if (this.degrade === 1) this._initParticles(Math.max(2000, this.nParticles >> 1));
        console.info("field: frame time over 14 ms for 2 s; reducing detail (level %d)", this.degrade);
      }
    } else this.slowSince = null;
  }

  _use(prog, setU) { this.gl.useProgram(prog.p); setU(prog.u); }

  _attrib(prog, name, buf, size, divisor = 0, stride = 0, offset = 0) {
    const gl = this.gl, loc = prog.a[name];
    if (loc === undefined || loc < 0) return;
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, size, gl.FLOAT, false, stride, offset);
    gl.vertexAttribDivisor(loc, divisor);
  }

  _resetAttribs() {
    const gl = this.gl;
    gl.bindVertexArray(null);
    const n = 8;
    for (let i = 0; i < n; i++) { gl.disableVertexAttribArray(i); gl.vertexAttribDivisor(i, 0); }
  }

  _tex(unit, tex, loc) { const gl = this.gl; gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, tex); gl.uniform1i(loc, unit); }

  _drawQuad(prog, setU) {
    const gl = this.gl;
    this._resetAttribs();
    gl.useProgram(prog.p);
    setU(prog.u);
    this._attrib(prog, "aPos", this.quad, 2);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
  }

  _sampleUniforms(u) {
    const gl = this.gl;
    this._tex(4, this.dataTex.den, u.uDen); this._tex(5, this.dataTex.press, u.uPress); this._tex(6, this.dataTex.vel, u.uVel);
    gl.uniform2f(u.uGrid, this.grid.cols, this.grid.rows); gl.uniform1f(u.uCell, this.grid.cell);
    gl.uniform2f(u.uArea, this.area.w, this.area.h); gl.uniform1f(u.uHScale, H_PER_DENSITY);
  }

  _drawTerrain(prog, vp, time, motion, futureAdd, setU) {
    const gl = this.gl;
    this._resetAttribs();
    gl.useProgram(prog.p);
    this._sampleUniforms(prog.u);
    gl.uniformMatrix4fv(prog.u.uVP, false, vp);
    gl.uniform1f(prog.u.uTime, time); gl.uniform1f(prog.u.uTremor, motion ? 1 : 0); gl.uniform1f(prog.u.uLift, futureAdd > 0 ? 0.02 : 0);
    gl.uniform1f(prog.u.uFutureAdd, futureAdd); gl.uniform1f(prog.u.uDmax, this.future?.dmax || 1);
    setU(prog.u);
    this._attrib(prog, "aXY", this.mesh.vb, 2);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.mesh.ib);
  }

  _drawTrails(vp, tok, A) {
    const gl = this.gl, segs = [], now = performance.now() / 1000;
    for (const [, h] of this.tracks) {
      if (h.length < 2) continue;
      for (let i = 1; i < h.length; i++) {
        const a = h[i - 1], b = h[i];
        const fa = Math.max(0, 1 - (now - a.w) / 3), fb = Math.max(0, 1 - (now - b.w) / 3);
        segs.push(a.x, this.densityAt(a.x, a.y) * H_PER_DENSITY + 0.1, a.y, fa * 0.22, b.x, this.densityAt(b.x, b.y) * H_PER_DENSITY + 0.1, b.y, fb * 0.22);
      }
    }
    if (!segs.length) return;
    this._resetAttribs();
    if (!this.trailBuf) this.trailBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.trailBuf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(segs), gl.DYNAMIC_DRAW);
    this._use(this.progs.line, (u) => { gl.uniformMatrix4fv(u.uVP, false, vp); gl.uniform2fv(u.uArea, A); gl.uniform3fv(u.uColor, tok.person); gl.uniform1f(u.uEmit, tok.night ? 0.3 : 0); gl.uniform1f(u.uAdditive, tok.night ? 1 : 0); });
    this._attrib(this.progs.line, "aV", this.trailBuf, 4);
    gl.drawArrays(gl.LINES, 0, segs.length / 4);
  }

  _drawPeople(vp, tok, A) {
    const gl = this.gl, list = this.trackList || [];
    if (!list.length) return;
    const inst = new Float32Array(list.length * 4), sel = new Float32Array(list.length);
    const dmax = Math.max(this.thresholds.red, 1);
    list.forEach((t, i) => {
      const d = this.densityAt(t.x, t.y);
      inst[i * 4] = t.x; inst[i * 4 + 1] = t.y; inst[i * 4 + 2] = d * H_PER_DENSITY; inst[i * 4 + 3] = clamp(d / dmax, 0, 1);
      sel[i] = this.selected && this.selected.id === t.id ? 1 : 0;
    });
    this._resetAttribs();
    if (!this.peopleBuf) { this.peopleBuf = gl.createBuffer(); this.selBuf = gl.createBuffer(); }
    gl.bindBuffer(gl.ARRAY_BUFFER, this.peopleBuf); gl.bufferData(gl.ARRAY_BUFFER, inst, gl.DYNAMIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.selBuf); gl.bufferData(gl.ARRAY_BUFFER, sel, gl.DYNAMIC_DRAW);
    const p = this.progs.people;
    this._use(p, (u) => { gl.uniformMatrix4fv(u.uVP, false, vp); gl.uniform2fv(u.uArea, A); gl.uniform3fv(u.uRight, this.right); gl.uniform3fv(u.uUp, this.up);
      gl.uniform1f(u.uSize, tok.night ? 0.3 : 0.22); gl.uniform3fv(u.uCool, tok.person); gl.uniform3fv(u.uWarmC, tok.personWarm);
      gl.uniform1f(u.uAdditive, tok.night ? 1 : 0); gl.uniform1f(u.uGlow, tok.glow); });
    this._attrib(p, "aCorner", this.corner, 2);
    this._attrib(p, "aI", this.peopleBuf, 4, 1);
    this._attrib(p, "aSel", this.selBuf, 1, 1);
    gl.drawArraysInstanced(gl.TRIANGLES, 0, 6, list.length);
  }

  _drawRings(vp, tok, time, motion) {
    const gl = this.gl, d = new Float32Array(this.rings.length * 4);
    this.rings.forEach((r, i) => { d[i * 4] = r.x; d[i * 4 + 1] = r.y; d[i * 4 + 2] = r.p; d[i * 4 + 3] = r.phase; });
    this._resetAttribs();
    if (!this.ringBuf) this.ringBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.ringBuf); gl.bufferData(gl.ARRAY_BUFFER, d, gl.DYNAMIC_DRAW);
    const p = this.progs.ring;
    gl.useProgram(p.p);
    this._sampleUniforms(p.u);
    gl.uniformMatrix4fv(p.u.uVP, false, vp); gl.uniform1f(p.u.uR, 3.2); gl.uniform1f(p.u.uTime, time); gl.uniform1f(p.u.uMoving, motion ? 1 : 0);
    gl.uniform3fv(p.u.uColor, tok.night ? tok.terrainHi : tok.crit);
    gl.disable(gl.DEPTH_TEST);
    this._attrib(p, "aCorner", this.corner, 2);
    this._attrib(p, "aI", this.ringBuf, 4, 1);
    gl.drawArraysInstanced(gl.TRIANGLES, 0, 6, this.rings.length);
    gl.enable(gl.DEPTH_TEST);
  }

  _particles(vp, tok, dt, time, motion, now) {
    const gl = this.gl, n = this.nParticles;
    const dst = this.pBufs[1 - this.pIdx];
    // update (skipped with reduced motion: particles become static streamlines)
    if (motion) {
      const pu = this.progs.pupd;
      gl.useProgram(pu.p);
      this._sampleUniforms(pu.u);
      gl.uniform1f(pu.u.uDt, dt); gl.uniform1f(pu.u.uTime, time); gl.uniform1f(pu.u.uSpeed, 1.0);
      gl.bindVertexArray(this.vaoUpd[this.pIdx]);
      gl.enable(gl.RASTERIZER_DISCARD);
      gl.bindBufferBase(gl.TRANSFORM_FEEDBACK_BUFFER, 0, dst);
      gl.beginTransformFeedback(gl.POINTS);
      gl.drawArrays(gl.POINTS, 0, n);
      gl.endTransformFeedback();
      gl.bindBufferBase(gl.TRANSFORM_FEEDBACK_BUFFER, 0, null);
      gl.disable(gl.RASTERIZER_DISCARD);
      gl.bindVertexArray(null);
      this.pIdx = 1 - this.pIdx;
    }
    // trail buffer: previous frame decays 6% (cleared when the camera moves), then new streaks
    const read = this.trailRead === "A" ? this.texTrailA : this.texTrailB;
    const writeFbo = this.trailRead === "A" ? this.trailFboB : this.trailFboA;
    const writeTex = this.trailRead === "A" ? this.texTrailB : this.texTrailA;
    gl.bindFramebuffer(gl.FRAMEBUFFER, writeFbo);
    gl.viewport(0, 0, this.W, this.H);
    const moving = now - this.cameraMovedAt < 120 || !motion;
    gl.disable(gl.BLEND);
    this._drawQuad(this.progs.fade, (u) => { this._tex(0, read, u.uTex); gl.uniform1f(u.uKeep, moving ? 0.0 : 0.94); });
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    const pr = this.progs.part;
    gl.useProgram(pr.p);
    this._sampleUniforms(pr.u);
    gl.uniformMatrix4fv(pr.u.uVP, false, vp); gl.uniform1f(pr.u.uStreak, motion ? 0.35 : 1.6); gl.uniform1f(pr.u.uSpeed, 1.0);
    // the trail keeps 94% per frame, so steady-state brightness is ~1/(1-0.94) = 17x one frame;
    // scale each frame's streaks so the accumulated trail stays in range
    gl.uniform3fv(pr.u.uColor, tok.particle);
    gl.uniform1f(pr.u.uIntensity, moving ? 0.35 : (tok.night ? 0.045 : 0.05));
    gl.bindVertexArray(this.vaoDraw[this.pIdx]);
    gl.drawArraysInstanced(gl.LINES, 0, 2, n);
    gl.bindVertexArray(null);
    gl.disable(gl.BLEND);
    this.trailRead = this.trailRead === "A" ? "B" : "A";
    this.trailTex = writeTex;
  }

  // ---------------------------------------------------------------- HTML labels
  _label(key, cls, text, x, y, h, extra) {
    let el = this.labels.get(key);
    if (!el) { el = document.createElement("div"); el.className = "lbl " + cls; this.labelsEl.appendChild(el); this.labels.set(key, el); }
    if (el._text !== text) { el.textContent = text; el._text = text; }
    if (extra) for (const [k, v] of Object.entries(extra)) if (el.dataset[k] !== v) el.dataset[k] = v;
    const p = this.project(x, y, h);
    if (!p || p[2] > 1) { el.style.display = "none"; }
    else { el.style.display = ""; el.style.transform = `translate(${p[0].toFixed(1)}px, ${p[1].toFixed(1)}px) translate(-50%, -50%)`; el.style.left = "0"; el.style.top = "0"; }
    el._seen = this._labelFrame;
  }

  _labels(now, time) {
    if (!this.area || !this.state) return;
    this._labelFrame = (this._labelFrame || 0) + 1;
    const s = this.state;
    // contour labels at the Building and Critical index lines
    for (const lvl of [this.thresholds.amber, this.thresholds.red]) {
      const c = this.contours?.[lvl];
      (c?.anchors || []).slice(0, 6).forEach((a, i) => this._label(`c${lvl}-${i}`, "contour", `${lvl} /m²`, a[0], a[1], lvl * H_PER_DENSITY + 0.25));
    }
    // gate: glows in the colour of the advisory
    const g = s.gate;
    if (g && isNum(g.y0)) {
      const gx = g.x ?? this.area.w, out = gx <= 0.01 ? -0.6 : 0.6;
      this._label("gate", "gate", g.label || "Gate", gx + out, (g.y0 + g.y1) / 2, 1.9, { level: s.status?.level || "GREEN" });
    }
    // benchmark at the peak cell
    const pk = s.density?.peak_cell;
    if (Array.isArray(pk) && isNum(s.density?.max) && s.density.max >= 0.5) {
      const x = (pk[1] + 0.5) * this.grid.cell, y = (pk[0] + 0.5) * this.grid.cell;
      this._label("peak", "peak", `${s.density.max.toFixed(1)}`, x, y, this.densityAt(x, y) * H_PER_DENSITY + 0.7);
    }
    // pressure multiple on each ring
    this.rings.slice(0, 3).forEach((r, i) => this._label(`ring${i}`, "ring", `${r.p.toFixed(1)}× pressure`, r.x, r.y + 2.2, this.densityAt(r.x, r.y) * H_PER_DENSITY + 0.1));
    // forecast countdown along the ghost's edge
    if (this.future && this.future.label) {
      const pkx = Array.isArray(pk) ? (pk[1] + 0.5) * this.grid.cell : this.area.w / 2, pky = Array.isArray(pk) ? (pk[0] + 0.5) * this.grid.cell : this.area.h / 2;
      this._label("future", "countdown", this.future.label, pkx, pky, (this.densityAt(pkx, pky) + this.future.add) * H_PER_DENSITY + 1.1);
    }
    for (const [k, el] of this.labels) if (el._seen !== this._labelFrame) { el.remove(); this.labels.delete(k); }
  }

  metresPerPixel() {
    if (!this.area) return null;
    const a = this.project(this.area.w / 2 - 2.5, this.area.h / 2, 0), b = this.project(this.area.w / 2 + 2.5, this.area.h / 2, 0);
    if (!a || !b) return null;
    return 5 / Math.max(1, Math.hypot(a[0] - b[0], a[1] - b[1]));
  }

  destroy() { this.ro?.disconnect(); this.worker?.terminate(); }
}
