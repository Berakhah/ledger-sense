"""Print-ready measured-cost report. The point estimate never appears without
its interval, and the method statement travels with the number."""

import html

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
    # method_statement() carries study.sampling_frame verbatim — free text
    # from an API caller, unconstrained by NewStudy's schema — straight into
    # this <pre> block. <pre> is an ordinary HTML container, not a raw-text
    # element like <script>/<style>: "</pre><script>..." breaks out of it.
    # Escaping the whole block rather than just sampling_frame at its point
    # of interpolation in study.py keeps this a render-boundary concern, not
    # something every future caller of method_statement() has to remember.
    method = html.escape(study.method_statement())
    return f"""<!doctype html>
<style>{_PRINT_CSS}</style>
<h1>Measured cost per document</h1>
<p class="figure"><strong>${point}</strong> per document
   (95% confidence interval ${lo} to ${hi})</p>
<h2>Method</h2>
<pre>{method}</pre>
<h2>Scope</h2>
<p>This figure describes the sampled documents only, over the stated collection
   period. It is not an industry average and is not comparable to published
   per-invoice cost figures, whose provenance we could not verify.</p>
"""
