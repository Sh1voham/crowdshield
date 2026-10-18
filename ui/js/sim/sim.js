// Built-in crowd simulator (SIMULATED data, always labelled as such).
// A small social-force model: people walk toward a gate in the right wall, repel each
// other and the walls, and queue when the gate cannot pass them. The simulator then runs
// the same field maths and Assessor as the engine and emits contract-shaped state.

import { Assessor, DEFAULT_CFG, ACTION } from "./assessor.js";
import { computeFields, gridShape, PressureBuffer, argmax, toRows } from "./fields.js";

export function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const clamp = (x, a, b) => (x < a ? a : x > b ? b : x);

export const SCENARIOS = {
  calm: {
    label: "Calm flow toward a 4 m gate", width: 24, height: 12, duration: 120,
    gate: (t) => ({ x: 24, y0: 4, y1: 8 }), cap: (t) => 5.0, inflow: (t) => 0.9, initial: 20, push: 0,
  },
  building: {
    label: "Inflow ramps toward a 2.2 m gate; a queue builds", width: 24, height: 12, duration: 200,
    gate: (t) => ({ x: 24, y0: 4.9, y1: 7.1 }), cap: (t) => 2.0, inflow: (t) => 1.0 + 3.2 * clamp(t / 110, 0, 1), initial: 30, push: 0.9,
  },
  surge: {
    label: "Sudden surge toward a 1.8 m gate", width: 24, height: 12, duration: 90,
    gate: (t) => ({ x: 24, y0: 5.1, y1: 6.9 }), cap: (t) => 1.6, inflow: (t) => (t < 8 ? 1.5 : t < 40 ? 11 : 3), initial: 60, push: 1.3,
  },
  relief: {
    label: "Packed crowd disperses when the gate opens to 7 m", width: 24, height: 12, duration: 80,
    gate: (t) => (t < 14 ? { x: 24, y0: 5.2, y1: 6.8 } : { x: 24, y0: 2.5, y1: 9.5 }), cap: (t) => (t < 14 ? 0.8 : 9.0), inflow: (t) => (t < 14 ? 2 : 0.4),
    initial: 300, packed: true, push: 1.1,
  },
};

export class Sim {
  constructor(name = "building", { seed = 7, cfg = DEFAULT_CFG, dt = 0.1, zone = null } = {}) {
    const sc = SCENARIOS[name];
    if (!sc) throw new Error(`unknown scenario ${name}`);
    this.name = name; this.sc = sc; this.dt = dt; this.rng = mulberry32(seed);
    this.cfg = cfg;
    this.zone = zone || `Gate A (simulated ${name})`;
    this.t = 0; this.nextId = 1; this.people = []; this.spawnAcc = 0; this.seq = 0; this.gateTokens = 1;
    this.shape = gridShape(sc.width, sc.height, 1.0);
    this.pbuf = new PressureBuffer(this.shape.rows * this.shape.cols, 8.0, 15, 3.0);
    this.assessor = new Assessor(cfg);
    this.nextField = 0;
    this.lastState = null;
    for (let i = 0; i < sc.initial; i++) this.spawn(true);
  }

  gauss() {
    const u = Math.max(1e-12, this.rng()), v = this.rng();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }

  spawn(initial = false) {
    const sc = this.sc;
    for (let tries = 0; tries < 12; tries++) {
      let x, y;
      if (initial && sc.packed) { x = sc.width - 9 + this.rng() * 8.6; y = 3 + this.rng() * 6; }
      else if (initial) { x = this.rng() * sc.width * 0.8; y = 0.6 + this.rng() * (sc.height - 1.2); }
      else { x = 0.2 + this.rng() * 0.8; y = 0.6 + this.rng() * (sc.height - 1.2); }
      const minD = initial && sc.packed ? 0.32 : 0.5;
      if (this.people.every((p) => (p.x - x) ** 2 + (p.y - y) ** 2 > minD * minD)) {
        const v0 = 1.15 + 0.25 * this.gauss();
        this.people.push({ id: this.nextId++, x, y, vx: 0.5, vy: 0, v0: clamp(v0, 0.7, 1.6), evx: null, evy: null, age: 0 });
        return true;
      }
    }
    return false;
  }

  step() {
    const sc = this.sc, dt = this.dt, t = this.t, gate = sc.gate(t);
    this.spawnAcc += sc.inflow(t) * dt;
    this.gateTokens = Math.min(2, this.gateTokens + sc.cap(t) * dt);
    while (this.spawnAcc >= 1) { this.spawnAcc -= 1; this.spawn(false); }

    // spatial hash (1 m cells) for neighbour forces and local density
    const hash = new Map();
    const key = (cx, cy) => cx * 1000 + cy;
    for (const p of this.people) {
      const k = key(Math.floor(p.x), Math.floor(p.y));
      let b = hash.get(k); if (!b) hash.set(k, (b = [])); b.push(p);
    }
    const A = 2.1, B = 0.28, rad = 0.23, tau = 0.5;
    const gy0 = gate.y0 + 0.35, gy1 = gate.y1 - 0.35;
    for (const p of this.people) {
      // desired direction: toward the nearest point of the gate opening
      const ty = clamp(p.y, gy0, Math.max(gy0, gy1));
      let dx = gate.x + 0.6 - p.x, dy = ty - p.y;
      const dn = Math.hypot(dx, dy) || 1;
      dx /= dn; dy /= dn;
      let fx = (p.v0 * dx - p.vx) / tau, fy = (p.v0 * dy - p.vy) / tau;
      let local = 0;
      const cx = Math.floor(p.x), cy = Math.floor(p.y);
      for (let i = -1; i <= 1; i++) for (let j = -1; j <= 1; j++) {
        const b = hash.get(key(cx + i, cy + j)); if (!b) continue;
        for (const q of b) {
          if (q === p) continue;
          const ex = p.x - q.x, ey = p.y - q.y, d = Math.hypot(ex, ey);
          if (d > 1.5 || d < 1e-6) continue;
          if (d < 1.0) local++;
          const f = A * Math.exp((2 * rad - d) / B);
          fx += (f * ex) / d; fy += (f * ey) / d;
        }
      }
      // walls: top, bottom, left, and the right wall outside the gate opening
      const wall = (dist, nx, ny) => { const f = 3 * Math.exp((rad - dist) / 0.2); fx += f * nx; fy += f * ny; };
      wall(p.y, 0, 1); wall(sc.height - p.y, 0, -1); wall(p.x, 1, 0);
      // the gate passes at most cap(t) people per second; the rest wait at the line
      const atGate = p.y >= gate.y0 && p.y <= gate.y1 && p.x > sc.width - 0.45;
      if (!p.passing && atGate && this.gateTokens >= 1) { this.gateTokens -= 1; p.passing = true; }
      if (!p.passing) wall(sc.width - p.x, -1, 0);
      // crowd turbulence: at high local density, pushes propagate as random impulses
      const dens = local / Math.PI;
      if (sc.push > 0 && dens > 3.5) {
        const s = sc.push * (dens - 3.5);
        fx += s * this.gauss(); fy += s * this.gauss();
      }
      p.ax = fx; p.ay = fy;
    }
    const keep = [];
    for (const p of this.people) {
      p.vx += p.ax * dt; p.vy += p.ay * dt;
      const sp = Math.hypot(p.vx, p.vy);
      if (sp > 1.9) { p.vx *= 1.9 / sp; p.vy *= 1.9 / sp; }
      p.x += p.vx * dt; p.y += p.vy * dt;
      p.y = clamp(p.y, 0.05, sc.height - 0.05);
      p.x = Math.max(0.05, p.x);
      if (p.passing) {
        if (p.x < sc.width + 0.4) keep.push(p);
      } else { if (p.x > sc.width - 0.05) p.x = sc.width - 0.05; keep.push(p); }
      // observed velocity: EMA like the engine's kinematics (tau 1 s)
      const a = 1 - Math.exp(-dt / 1.0);
      if (p.evx === null) { p.evx = p.vx; p.evy = p.vy; } else { p.evx += a * (p.vx - p.evx); p.evy += a * (p.vy - p.evy); }
      p.age += dt;
    }
    this.people = keep;
    this.t = Math.round((t + dt) * 1e6) / 1e6;
    if (this.t + 1e-9 >= this.nextField) {
      this.nextField += 0.2;
      this.lastState = this.makeState();
      return this.lastState;
    }
    return null;
  }

  makeState() {
    const sc = this.sc, shape = this.shape;
    const inside = this.people.filter((p) => p.x <= sc.width);
    const pts = inside.map((p) => (p.age >= 0.5 ? { x: p.x, y: p.y, vx: p.evx, vy: p.evy } : { x: p.x, y: p.y, vx: null, vy: null }));
    const f = computeFields(pts, shape);
    const P = this.pbuf.update(this.t, f.vx, f.vy, f.rho);
    const k = argmax(f.rho);
    const dmax = f.rho[k];
    let pmax = 0, psum = 0, pn = 0, rsorted = Array.from(f.rho).sort((a, b) => a - b);
    for (let i = 0; i < P.length; i++) { if (P[i] > pmax) pmax = P[i]; if (f.rho[i] >= 1) { psum += P[i]; pn++; } }
    const pthr = 0.02, pidx = pmax / pthr;
    const a = this.assessor.step(this.t, dmax, pidx);
    const speeds = pts.filter((p) => p.vx !== null).map((p) => Math.hypot(p.vx, p.vy));
    const r2 = (x, n) => (x === null || x === undefined || !Number.isFinite(x) ? null : Math.round(x * 10 ** n) / 10 ** n);
    const gate = sc.gate(this.t);
    this.seq++;
    const p95 = rsorted[Math.min(rsorted.length - 1, Math.floor(0.95 * (rsorted.length - 1)))];
    return {
      v: 1, type: "state", seq: this.seq, t: r2(this.t, 3), ts: new Date().toISOString(), zone: this.zone,
      area: { width_m: sc.width, height_m: sc.height },
      gate: { x: gate.x, y0: gate.y0, y1: gate.y1, label: "Gate A" },
      status: { level: a.status, label: a.label, since_t: r2(a.status_since, 3), raw: a.raw, cause: a.cause },
      action: a.action, count: inside.length,
      density: { max: r2(dmax, 3), mean: r2(inside.length / (sc.width * sc.height), 3), p95: r2(p95, 3),
                 peak_cell: [Math.floor(k / shape.cols), k % shape.cols], measured_max: r2(dmax, 3), unit: "persons/m2" },
      pressure: { max: r2(pmax, 5), mean: r2(pn ? psum / pn : 0, 5), threshold: pthr, index: r2(pidx, 3) },
      speed: { mean: r2(speeds.length ? speeds.reduce((s, v) => s + v, 0) / speeds.length : null, 3) },
      turbulence: { on: a.turb_on, since_t: r2(a.turb_since, 3) },
      forecast: { trend_per_min: r2(a.trend_per_min, 3), t_amber_s: r2(a.t_amber_s, 1), t_red_s: r2(a.t_red_s, 1), r2: r2(a.r2, 3) },
      risk: r2(a.risk, 3),
      thresholds: { ...this.cfg.status, p_thr: pthr, profile: "default" },
      grid: { cell_m: 1.0, rows: shape.rows, cols: shape.cols, density: toRows(f.rho, shape.rows, shape.cols, 3),
              pressure: toRows(P, shape.rows, shape.cols, 5), speed: toRows(f.speed, shape.rows, shape.cols, 3),
              vx: toRows(f.vx, shape.rows, shape.cols, 3), vy: toRows(f.vy, shape.rows, shape.cols, 3) },
      tracks: inside.slice(0, 400).map((p) => ({ id: p.id, x: r2(p.x, 2), y: r2(p.y, 2),
        vx: p.age >= 0.5 ? r2(p.evx, 2) : null, vy: p.age >= 0.5 ? r2(p.evy, 2) : null })),
      pipeline: { source: "sim", fps: 10, latency_ms: null, frames: Math.round(this.t / this.dt), model: null,
                  precision: null, layout: "simulator", tracker: "simulator" },
      actuator: null,
      _events: a.events, _raw: { dmax, pidx, assess: a },
    };
  }

  done() { return this.t >= this.sc.duration; }
}

export { ACTION };
