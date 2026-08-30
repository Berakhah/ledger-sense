import type { Metadata } from 'next'
import { tokens } from '../lib/tokens'
import './globals.css'

export const metadata: Metadata = {
  title: 'Ledger Sense',
  description: 'Document intelligence with confidence routing, for construction progress billing.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // The density and scheme defaults are read from tokens.ts rather than
  // hardcoded here, so the value asserted by tokens.test.ts is the value the
  // document actually boots with. A density control (Phase 4) reassigns this
  // attribute; globals.css keys the row metrics off it.
  return (
    <html lang="en" data-density={tokens.defaultDensity} data-scheme={tokens.defaultScheme}>
      <body>{children}</body>
    </html>
  )
}
