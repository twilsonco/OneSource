"""VinylLabels — layout, metrics, and reporting for wide-roll vinyl label jobs.

The package root is deliberately import-light: importing :mod:`vinyllabels`
pulls in no third-party dependencies. Import the submodule you need, for
example :mod:`vinyllabels.layout` or :mod:`vinyllabels.metrics`.

Entry points:

* :mod:`vinyllabels.generate` — lay out label codes and emit PDF + reports
  (console script ``generate``).
* :mod:`vinyllabels.consolidate` — merge many job reports into one
  (console script ``consolidate``).
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
