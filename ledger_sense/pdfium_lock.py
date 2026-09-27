"""One process-wide lock for every in-process PDFium call.

PDFium is not thread-safe — not even across different documents — and
FastAPI runs sync endpoints on a thread pool, so two requests that both touch
pypdfium2 at once can crash the process natively (the demo API exited 139
under parallel seeding). Hold this lock for the whole lifetime of any PDFium
object, including its close: a document left to the garbage collector is
closed by a finaliser on whichever thread happens to run it, outside the lock.

ingest/limits.py needs none of this — it parses in a spawned child process.
"""

import threading

PDFIUM_LOCK = threading.Lock()
