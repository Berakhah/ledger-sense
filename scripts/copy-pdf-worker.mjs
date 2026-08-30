// Self-host the pdf.js worker. Runs as `predev`/`prebuild`.
//
// No CDN, for the reason 00 §5.2 gives for self-hosting fonts: the demo has to
// run with no network, and a request to someone else's server for the worker
// that renders a client's pay application tells that server which document is
// being read, and when. The file is copied from node_modules rather than
// committed — it is a build output of a pinned dependency, so it is
// .gitignored and reproduced by `npm install && npm run build`.

import { copyFileSync, mkdirSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')

// Resolved through node, not hand-written: the path inside pdfjs-dist has
// moved between majors, and a wrong literal would fail at runtime in the
// browser instead of here.
const source = join(dirname(require.resolve('pdfjs-dist/package.json')),
                    'build', 'pdf.worker.min.mjs')
const target = join(root, 'ledger_sense', 'web', 'public', 'pdf.worker.min.mjs')

mkdirSync(dirname(target), { recursive: true })
copyFileSync(source, target)
console.log(`pdf.js worker -> ${target}`)
