'use client';

/**
 * Which way through the product the user chose for a given paper.
 *
 * Two things depend on this, and they are different:
 *
 *  - A refresh on `/documents/{id}/chat` must not re-ask. The choice was made
 *    on the threshold and the threshold is gone; re-deriving it from the URL
 *    would work, but only until the user opens the guided screens and comes
 *    back, at which point the URL says nothing about what they picked.
 *
 *  - The chat screen can tell **"you chose this"** from **"you typed the URL"**.
 *    They deserve different first paragraphs: one is the flow continuing, the
 *    other is someone arriving at a conversation that may not exist yet.
 *
 * `sessionStorage`, not `localStorage`: the choice belongs to this visit and
 * this paper. A preference silently remembered across weeks is a preference the
 * user cannot see and did not ask for.
 */

export type Flow = 'guided' | 'conversational';

const KEY = (docId: string) => `answerthat.flow.${docId}`;

const isFlow = (v: string | null): v is Flow => v === 'guided' || v === 'conversational';

export function rememberFlow(docId: string, flow: Flow): void {
  if (typeof sessionStorage === 'undefined') return;
  try {
    sessionStorage.setItem(KEY(docId), flow);
  } catch {
    // Storage disabled or full. The screens all work without it — the chat page
    // simply treats the visit as arrived-by-URL, which is the honest reading
    // when we genuinely do not know.
  }
}

export function recallFlow(docId: string): Flow | null {
  if (typeof sessionStorage === 'undefined') return null;
  try {
    const raw = sessionStorage.getItem(KEY(docId));
    return isFlow(raw) ? raw : null;
  } catch {
    return null;
  }
}

/** Where each route goes. One definition, so the fork and the links agree. */
export const FLOW_PATH: Record<Flow, (docId: string) => string> = {
  guided: (docId) => `/documents/${docId}/parse`,
  conversational: (docId) => `/documents/${docId}/chat`,
};
