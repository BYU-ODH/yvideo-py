// Snapping a dragged timeline edge onto the playhead (#443).
//
// The workflow this serves: park the playhead on the exact frame (`,` / `.`), then drag an item or
// one of its handles to it. Distances are measured in screen pixels, not seconds, so the pull feels
// the same at every zoom level and zooming in is a natural way to place an edge close to (but not
// on) the playhead.
//
// Snapping has hysteresis: an edge is caught within SNAP_ACQUIRE_PX but only lets go beyond
// SNAP_RELEASE_PX. Without it, an edge hovering at the threshold flickers in and out of the snap;
// with it, leaving the snap takes a deliberate move, and once out the edge is placed freely
// until it comes back within the acquire distance. No modifier keys are involved.

export const SNAP_ACQUIRE_PX = 8;
export const SNAP_RELEASE_PX = 14;

/**
 * Which dragged edge, if any, snaps to the target.
 *
 * @param {Array<number|null>} edges Pointer-driven (unsnapped) pixel positions of the dragged
 *   edges, e.g. [start] for a handle, [start, end] for a whole item. Null entries are skipped.
 * @param {number} target Pixel position of the playhead.
 * @param {number} current Index of the edge snapped on the previous move, or -1.
 * @returns {number} Index of the edge to snap, or -1 for none.
 */
export function pickSnapEdge(edges, target, current = -1, {
  acquire = SNAP_ACQUIRE_PX,
  release = SNAP_RELEASE_PX,
} = {}) {
  if (current >= 0 && edges[current] != null && Math.abs(edges[current] - target) <= release) {
    return current;
  }
  let best = -1;
  let bestDistance = acquire;
  edges.forEach((edge, index) => {
    if (edge == null) return;
    const distance = Math.abs(edge - target);
    if (distance <= bestDistance) {
      best = index;
      bestDistance = distance;
    }
  });
  return best;
}

/**
 * The edge to preview in the player while nothing is snapped: whichever is closest to the
 * target, since that is the one the user is presumably lining up.
 */
export function nearestEdge(edges, target) {
  let best = -1;
  let bestDistance = Infinity;
  edges.forEach((edge, index) => {
    if (edge == null) return;
    const distance = Math.abs(edge - target);
    if (distance < bestDistance) {
      best = index;
      bestDistance = distance;
    }
  });
  return best;
}
