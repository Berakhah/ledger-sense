// The route every Phase 4 Playwright test navigates to. It exists now so the
// seeded fixture (POST /api/fixtures/seed, demo profile only) has somewhere to
// land; the split view, the field boxes and the J/K/Enter/E/R keyboard path
// are Phase 4's and replace this body.
export default async function ReviewQueue({
  params,
}: {
  params: Promise<{ docId: string }>
}) {
  const { docId } = await params
  return (
    <main style={{ padding: '2rem' }}>
      <h1 style={{ fontSize: '1.25rem', fontWeight: 600 }}>Review queue</h1>
      <p>
        Document <span className="numeric" data-testid="doc-id">{docId}</span>
      </p>
      <p style={{ color: 'var(--color-ink-muted)' }}>
        Scaffold. The split view, provenance rail and keyboard path are Phase 4.
      </p>
    </main>
  )
}
