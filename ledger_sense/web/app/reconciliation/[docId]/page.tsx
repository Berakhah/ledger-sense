import { Reconciliation } from './Reconciliation'

// Thin on purpose: awaits the dynamic segment and hands it to the client
// component. Everything that fetches or renders lives in Reconciliation.tsx.
// Mirrors app/review/[docId]/page.tsx.
export default async function ReconciliationPage({
  params,
}: {
  params: Promise<{ docId: string }>
}) {
  const { docId } = await params
  // Keyed: a client-side navigation between documents remounts the screen
  // (fresh fetch) instead of showing the previous document's data.
  return <Reconciliation key={docId} docId={docId} />
}
