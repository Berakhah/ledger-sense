import Link from 'next/link'

// Each card replaces the scaffold's bare link. The review queue entry points
// at the seeded demo document; the reconciliation and vendor screens are
// reached from a document and a vendor, so they have no root entry.
const SCREENS = [
  {
    href: '/baseline',
    label: 'Baseline study',
    note: 'Measure what the current process costs, before anything is automated.',
  },
  {
    href: '/inbox',
    label: 'Inbox',
    note: 'Every document received, with its routing outcome and open flags.',
  },
  {
    href: '/review/doc-50',
    label: 'Review queue',
    note: 'Clear exceptions from the keyboard. Accepted fields keep their rule.',
  },
] as const

export default function Home() {
  return (
    <main className="mx-auto max-w-4xl px-6 py-16">
      <p className="text-sm tracking-widest text-ink-muted">LEDGER SENSE</p>
      <h1 className="mt-3 text-5xl font-semibold leading-tight">
        Read the bill.
        <br />
        <span className="text-ink-muted">Check the arithmetic.</span>
      </h1>
      <p className="mt-6 max-w-xl text-ink-muted">
        Document intelligence for construction progress billing. The deterministic path
        extracts and validates; anything it cannot stand behind goes to a person, with the
        reason shown.
      </p>

      <ul className="mt-12 grid gap-4 sm:grid-cols-3">
        {SCREENS.map(({ href, label, note }) => (
          <li key={href}>
            <Link
              href={href}
              className="group block h-full rounded-lg border border-rule p-5 no-underline transition-transform duration-150 hover:-translate-y-0.5 hover:border-ink-muted motion-reduce:transition-none motion-reduce:hover:translate-y-0"
              style={{ background: 'var(--surface)', boxShadow: 'var(--shadow-lift)' }}
            >
              <span className="text-lg font-medium">{label}</span>
              <span className="mt-2 block text-sm text-ink-muted">{note}</span>
              <span aria-hidden="true" className="mt-4 block text-ink-muted transition-transform duration-150 group-hover:translate-x-1">
                &rarr;
              </span>
            </Link>
          </li>
        ))}
      </ul>

      <p className="mt-16 text-xs text-ink-muted">
        Not developed, endorsed, approved, sponsored by or affiliated with the American
        Institute of Architects. AIA&reg;, G702&reg; and G703&reg; are registered trademarks
        of the AIA; references describe compatibility only.
      </p>
    </main>
  )
}
