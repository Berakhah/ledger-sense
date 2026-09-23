import { VendorLedger } from './VendorLedger'

// Thin on purpose, mirrors app/reconciliation/[docId]/page.tsx: awaits the
// dynamic segment, hands it to the client component that does the fetching.
export default async function VendorLedgerPage({
  params,
}: {
  params: Promise<{ vendorId: string }>
}) {
  const { vendorId } = await params
  return <VendorLedger key={vendorId} vendorId={vendorId} />
}
