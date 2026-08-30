import Link from 'next/link'

// Scaffold only. The six real screens — baseline study, inbox, review queue,
// reconciliation, vendor ledger, metrics — are Phase 4's, and each replaces
// its entry here rather than being added alongside.
const SCREENS = [
  { href: '/review/doc-50', label: 'Review queue' },
] as const

export default function Home() {
  return (
    <main style={{ padding: '2rem', maxWidth: '48rem' }}>
      <h1 style={{ fontSize: '1.5rem', fontWeight: 600 }}>Ledger Sense</h1>
      <p style={{ color: 'var(--color-ink-muted)' }}>
        Scaffold. Screens land in Phase 4.
      </p>
      <ul>
        {SCREENS.map(({ href, label }) => (
          <li key={href}>
            <Link href={href}>{label}</Link>
          </li>
        ))}
      </ul>
    </main>
  )
}
