import { ReviewQueue } from './ReviewQueue'

// Thin on purpose: awaits the dynamic segment and hands it to the client
// component. Everything that fetches, renders or listens lives in
// ReviewQueue.tsx.
export default async function ReviewPage({
  params,
}: {
  params: Promise<{ docId: string }>
}) {
  const { docId } = await params
  // Keyed: a client-side navigation between documents remounts the queue
  // (fresh model, guards and type-ahead) instead of acting on stale refs.
  return <ReviewQueue key={docId} docId={docId} />
}
