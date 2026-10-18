// Marching squares on a cell-centred scalar grid. Returns polylines in floor metres.
// Pure functions (used by the Web Worker, the 2D renderer, reality lock and tests).

export function isolines(data, rows, cols, cellM, level) {
  const segs = [];
  const val = (r, c) => data[r * cols + c];
  const px = (c) => (c + 0.5) * cellM, py = (r) => (r + 0.5) * cellM;
  const interp = (x1, y1, v1, x2, y2, v2) => {
    const t = v2 === v1 ? 0.5 : (level - v1) / (v2 - v1);
    return [x1 + (x2 - x1) * t, y1 + (y2 - y1) * t];
  };
  // pad the grid with a border below the level so contours close at the area edge
  const R = rows + 2, C = cols + 2;
  const v = (r, c) => (r <= 0 || c <= 0 || r >= R - 1 || c >= C - 1) ? -1e9 : val(r - 1, c - 1);
  const X = (c) => px(c - 1), Y = (r) => py(r - 1);
  for (let r = 0; r < R - 1; r++) {
    for (let c = 0; c < C - 1; c++) {
      const a = v(r, c), b = v(r, c + 1), cc = v(r + 1, c + 1), d = v(r + 1, c);
      let idx = 0;
      if (a >= level) idx |= 8;
      if (b >= level) idx |= 4;
      if (cc >= level) idx |= 2;
      if (d >= level) idx |= 1;
      if (idx === 0 || idx === 15) continue;
      const lo = (val1) => Math.max(val1, level - 1e6);
      const top = () => interp(X(c), Y(r), lo(a), X(c + 1), Y(r), lo(b));
      const right = () => interp(X(c + 1), Y(r), lo(b), X(c + 1), Y(r + 1), lo(cc));
      const bottom = () => interp(X(c), Y(r + 1), lo(d), X(c + 1), Y(r + 1), lo(cc));
      const left = () => interp(X(c), Y(r), lo(a), X(c), Y(r + 1), lo(d));
      const center = (a + b + cc + d) / 4;
      switch (idx) {
        case 1: case 14: segs.push([left(), bottom()]); break;
        case 2: case 13: segs.push([bottom(), right()]); break;
        case 3: case 12: segs.push([left(), right()]); break;
        case 4: case 11: segs.push([top(), right()]); break;
        case 6: case 9: segs.push([top(), bottom()]); break;
        case 7: case 8: segs.push([left(), top()]); break;
        case 5: if (center >= level) { segs.push([left(), top()]); segs.push([bottom(), right()]); } else { segs.push([left(), bottom()]); segs.push([top(), right()]); } break;
        case 10: if (center >= level) { segs.push([top(), right()]); segs.push([left(), bottom()]); } else { segs.push([left(), top()]); segs.push([bottom(), right()]); } break;
      }
    }
  }
  return joinSegments(segs, area(rows, cols, cellM));
}

function area(rows, cols, cellM) { return { w: cols * cellM, h: rows * cellM }; }

// Clamp edge points into the area and join segments into polylines.
function joinSegments(segs, ar) {
  const key = (p) => `${Math.round(p[0] * 1e4)},${Math.round(p[1] * 1e4)}`;
  const clampP = (p) => [Math.min(ar.w, Math.max(0, p[0])), Math.min(ar.h, Math.max(0, p[1]))];
  const ends = new Map();
  const add = (k, i) => { if (!ends.has(k)) ends.set(k, []); ends.get(k).push(i); };
  segs.forEach((s, i) => { add(key(s[0]), i); add(key(s[1]), i); });
  const used = new Uint8Array(segs.length), lines = [];
  for (let i = 0; i < segs.length; i++) {
    if (used[i]) continue;
    used[i] = 1;
    const line = [segs[i][0], segs[i][1]];
    for (const dir of [1, 0]) {
      for (;;) {
        const tip = dir ? line[line.length - 1] : line[0];
        const cand = (ends.get(key(tip)) || []).find((j) => !used[j]);
        if (cand === undefined) break;
        used[cand] = 1;
        const s = segs[cand];
        const next = key(s[0]) === key(tip) ? s[1] : s[0];
        if (dir) line.push(next); else line.unshift(next);
      }
    }
    const closed = line.length > 3 && key(line[0]) === key(line[line.length - 1]);
    lines.push({ points: line.map(clampP), closed });
  }
  return lines;
}

export function polylineLength(pts) {
  let L = 0;
  for (let i = 1; i < pts.length; i++) L += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  return L;
}

// Label anchors every `spacing` metres along each line (at least one per line longer than minLen).
export function labelAnchors(lines, spacing = 6, minLen = 1.5) {
  const out = [];
  for (const ln of lines) {
    const pts = ln.points, L = polylineLength(pts);
    if (L < minLen) continue;
    const n = Math.max(1, Math.floor(L / spacing));
    for (let k = 0; k < n; k++) {
      let target = (k + 0.5) * (L / n), acc = 0;
      for (let i = 1; i < pts.length; i++) {
        const seg = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
        if (acc + seg >= target) {
          const t = seg ? (target - acc) / seg : 0;
          out.push([pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * t, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * t]);
          break;
        }
        acc += seg;
      }
    }
  }
  return out;
}

export function pointInPolygon(x, y, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
