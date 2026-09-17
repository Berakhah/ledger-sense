import type { BBox } from './bbox'

/**
 * The signature detail: an accepted field's rule STAYS, so the document
 * visually accumulates verification as the reviewer works through it.
 *
 * The server's exception list is the source of truth for which fields are
 * verified — it survives navigation, tabs and reviewers, and it is the same
 * record the audit trail was written from. So the rules are DERIVED from that
 * list, never kept in a browser-side copy that could disagree with it.
 */

export type ExceptionState = 'open' | 'accepted' | 'edited' | 'rejected'

/** The slice of ReviewException the rule needs. */
export interface ExceptionLike {
  id: string
  page: number | null
  bbox: BBox | null
  state: ExceptionState
}

export interface AcceptedBox {
  id: string
  page: number
  bbox: BBox
}

/** Every verified field with located geometry, in queue order. A rejected
 *  field was not verified, so it gets no rule. */
export function acceptedBoxesOf(items: ReadonlyArray<ExceptionLike>): AcceptedBox[] {
  const boxes: AcceptedBox[] = []
  for (const item of items) {
    if (item.state !== 'accepted' && item.state !== 'edited') continue
    if (item.page === null || item.bbox === null) continue
    boxes.push({ id: item.id, page: item.page, bbox: item.bbox })
  }
  return boxes
}
