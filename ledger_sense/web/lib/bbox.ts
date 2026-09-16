/**
 * Phase 2 normalised every bbox to a TOP-LEFT origin in PDF points.
 * pdf.js renders at a scale where CSS pixels = PDF points x scale.
 *
 * So: no flipping, no page-height subtraction. If boxes appear mirrored
 * vertically, the bug is in extract/textlayer.py, not here.
 */

export type BBox = readonly [x0: number, top: number, x1: number, bottom: number]

export interface OverlayStyle {
  left: number
  top: number
  width: number
  height: number
}

export function toOverlayStyle(bbox: BBox, scale: number): OverlayStyle {
  const [x0, top, x1, bottom] = bbox
  return {
    left: x0 * scale,
    top: top * scale,
    width: Math.max(0, x1 - x0) * scale,
    height: Math.max(0, bottom - top) * scale,
  }
}
