// Grid fields in the browser: the same kernels as crowdshield/fields.py.
//   rho(r)   = 1/(pi R^2) * sum_j exp(-|r_j - r|^2 / R^2)   (cut-off 3R)
//   V(r)     = sum_j w_j v_j / sum_j w_j   (null if sum w < 1)
//   speed(r) = sum_j w_j |v_j| / sum_j w_j (null if sum w < 0.3)
//   P(r)     = rho * Var_t(V)  over 8 s, >= 15 valid samples, rho >= gate
// Used by the simulator, and by the UI to derive a velocity field from tracks when a
// state message carries no vx/vy grids.

export function gridShape(widthM, heightM, cellM = 1.0) {
  return { cols: Math.max(1, Math.ceil(widthM / cellM - 1e-9)), rows: Math.max(1, Math.ceil(heightM / cellM - 1e-9)), cellM };
}

// people: array of {x, y, vx, vy} (vx/vy may be null). Returns Float64Arrays (rows*cols, row-major).
export function computeFields(people, shape, R = 1.0, cutoffR = 3.0, vMinW = 1.0, sMinW = 0.3) {
  const { rows, cols, cellM } = shape;
  const n = rows * cols;
  const rho = new Float64Array(n), wsum = new Float64Array(n);
  const sx = new Float64Array(n), sy = new Float64Array(n), ss = new Float64Array(n);
  const R2 = R * R, cut = cutoffR * R, cut2 = cut * cut, norm = 1 / (Math.PI * R2);
  for (const p of people) {
    const c0 = Math.max(0, Math.floor((p.x - cut) / cellM)), c1 = Math.min(cols - 1, Math.floor((p.x + cut) / cellM));
    const r0 = Math.max(0, Math.floor((p.y - cut) / cellM)), r1 = Math.min(rows - 1, Math.floor((p.y + cut) / cellM));
    const hasV = p.vx !== null && p.vx !== undefined && p.vy !== null && p.vy !== undefined;
    const sp = hasV ? Math.hypot(p.vx, p.vy) : 0;
    for (let r = r0; r <= r1; r++) {
      const dy = p.y - (r + 0.5) * cellM;
      for (let c = c0; c <= c1; c++) {
        const dx = p.x - (c + 0.5) * cellM;
        const d2 = dx * dx + dy * dy;
        if (d2 > cut2) continue;
        const w = Math.exp(-d2 / R2);
        const k = r * cols + c;
        rho[k] += w;
        if (hasV) { wsum[k] += w; sx[k] += w * p.vx; sy[k] += w * p.vy; ss[k] += w * sp; }
      }
    }
  }
  const vx = new Float64Array(n), vy = new Float64Array(n), speed = new Float64Array(n);
  for (let k = 0; k < n; k++) {
    rho[k] *= norm;
    vx[k] = wsum[k] >= vMinW ? sx[k] / wsum[k] : NaN;
    vy[k] = wsum[k] >= vMinW ? sy[k] / wsum[k] : NaN;
    speed[k] = wsum[k] >= sMinW ? ss[k] / wsum[k] : NaN;
  }
  return { rho, vx, vy, speed, wsum, rows, cols };
}

export class PressureBuffer {
  constructor(n, windowS = 8.0, minSamples = 15, minDensity = 3.0) {
    this.n = n; this.window = windowS; this.minSamples = minSamples; this.minDensity = minDensity;
    this.samples = [];
  }
  reset() { this.samples = []; }
  update(t, vx, vy, rho) {
    this.samples.push({ t, vx: Float64Array.from(vx), vy: Float64Array.from(vy) });
    while (this.samples.length && t - this.samples[0].t > this.window + 1e-9) this.samples.shift();
    const n = this.n, P = new Float64Array(n);
    for (let k = 0; k < n; k++) {
      let cnt = 0, mx = 0, my = 0;
      for (const s of this.samples) { const a = s.vx[k], b = s.vy[k]; if (Number.isFinite(a) && Number.isFinite(b)) { cnt++; mx += a; my += b; } }
      if (cnt < this.minSamples || rho[k] < this.minDensity) continue;
      mx /= cnt; my /= cnt;
      let dev = 0;
      for (const s of this.samples) { const a = s.vx[k], b = s.vy[k]; if (Number.isFinite(a) && Number.isFinite(b)) dev += (a - mx) ** 2 + (b - my) ** 2; }
      P[k] = rho[k] * (dev / cnt);
    }
    return P;
  }
}

export function argmax(a) {
  let best = 0;
  for (let k = 1; k < a.length; k++) if (a[k] > a[best]) best = k;
  return best;
}

export function toRows(a, rows, cols, nd) {
  const f = 10 ** nd, out = [];
  for (let r = 0; r < rows; r++) {
    const row = [];
    for (let c = 0; c < cols; c++) { const v = a[r * cols + c]; row.push(Number.isFinite(v) ? Math.round(v * f) / f : null); }
    out.push(row);
  }
  return out;
}
