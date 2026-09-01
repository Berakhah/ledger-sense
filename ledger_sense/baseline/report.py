"""Print-ready measured-cost report. The point estimate never appears without
its interval, and the method statement travels with the number."""

from ledger_sense.baseline.study import BaselineStudy

_PRINT_CSS = """
@media print {
  body { font-family: Newsreader, Georgia, serif; color: #1a1a1a; }
  .no-print { display: none; }
  .figure { font-variant-numeric: tabular-nums; }
}
"""


def render_report(study: BaselineStudy) -> str:
    point = study.cost_per_document()
    lo, hi = study.confidence_interval()
    return f"""<!doctype html>
<style>{_PRINT_CSS}</style>
<h1>Measured cost per document</h1>
<p class="figure"><strong>${point}</strong> per document
   (95% confidence interval ${lo} to ${hi})</p>
<h2>Method</h2>
<pre>{study.method_statement()}</pre>
<h2>Scope</h2>
<p>This figure describes the sampled documents only, over the stated collection
   period. It is not an industry average and is not comparable to published
   per-invoice cost figures, whose provenance we could not verify.</p>
"""
