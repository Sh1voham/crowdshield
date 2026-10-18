// Web Worker: contour lines and label anchors for the Building (4) and Critical (6) levels.
import { isolines, labelAnchors } from "./contours.js";
import { upsample } from "./math.js";

self.onmessage = (ev) => {
  const { id, data, rows, cols, cellM, levels, k } = ev.data;
  const up = upsample(data, rows, cols, cellM, k || 4);
  const out = {};
  for (const level of levels) {
    const lines = isolines(up.data, up.rows, up.cols, up.cellM, level);
    out[level] = { lines, anchors: labelAnchors(lines, 6, 1.5) };
  }
  self.postMessage({ id, out });
};
