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
  return <ReviewQueue docId={docId} />
}
