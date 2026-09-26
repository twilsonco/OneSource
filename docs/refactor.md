# VinylLabels refactor

A record of the migration that turned the two monolithic vinyl-label scripts into
the `vinyllabels` package. It covers why, what the new shape is, how the
equivalence of the output was verified, and the three places where the output
intentionally changed.

---

## Before

Two standalone scripts carried the whole pipeline:

| File | Lines |
| --- | --- |
| `scripts/vinyl_label_prep.py` | 2 636 |
| `scripts/vinyl_label_multi_job_report.py` | 3 621 |

Both were self-contained: each re-declared its own layout constants, pricing
resolution, output-path organisation, ink measurement, and — the real source of
the bulk — its own copy of "render a table" for every report. Adding one column
to the consolidated report meant touching the header string, the separator
string, the row string, the totals string, the customer variant of all four, and
the matching CSV writer.

Concretely the duplication was:

* **Table rendering, ~15 times.** Every report hand-built its header, `-`/`=`
  rules, row lines, and totals line with f-strings, and repeated the whole thing
  for the customer subset. The legacy off-by-one in the consolidated
  PER-LABEL BREAKDOWN rule (see *Intentional differences*) is exactly the class
  of bug this style invites.
* **Pricing.** The flag set, the config discovery, and the override merge existed
  in both scripts, and only one of them actually honoured the numeric overrides.
* **Cost arithmetic.** `ink + substrate + printer + labor` and the markup step
  were re-derived in each report writer, so a report's total could disagree with
  the lines printed above it.
* **Output paths.** The `PDF Files/`, `Job Report/`, `json/`, `csv/`,
  `Multi-Job Report/`, `PDF Files for Vendor/` layout was spelled out inline in
  each writer.
* **Report loading.** The consolidated tool re-parsed the per-job JSON by hand,
  with no schema in one place.

## After

One package under `src/`, two entry points, and the two remaining one-off
scripts untouched:

```
src/vinyllabels/
├── __init__.py            import-light root (no third-party imports)
├── cli.py                 shared pricing flags -> PricingConfig
├── colors.py              preset / rgb / hex colour parsing
├── drawing.py             PDF construction (reportlab)
├── fonts.py               bold-font probing and registration
├── generate.py            `generate` entry point (was vinyl_label_prep.py)
├── ink.py                 glyph ink area (AreaPen / Green's theorem)
├── job_report.py          per-job text + JSON + CSV reports
├── labels.py              input parsing and glob expansion
├── layout.py              JobConfig: every geometry constant, derived grid
├── metrics.py             per-label and global metrics
├── models.py              LabelMetrics, GlobalMetrics, CostBreakdown, PricingConfig
├── output_paths.py        the one definition of where artifacts land
├── pricing.py             config discovery, resolution, cost breakdown
├── sizes.py               LabelSize and its rendering
├── units.py               sq_in -> sq_ft and friends
├── consolidate/
│   ├── cli.py             `consolidate` entry point (was vinyl_label_multi_job_report.py)
│   ├── csv_reports.py     the six CSV breakdowns, internal/customer/vendor
│   ├── load.py            *_report.json -> JobReport
│   ├── merge.py           consolidation arithmetic
│   ├── model.py           JobReport, ConsolidatedMetrics, SizeAreas, PdfFileRecord
│   └── report.py          consolidated text report + per-label listings
└── reportio/
    ├── loaders.py         shared JSON field readers, cost-breakdown loader
    ├── table.py           the table engine (see below)
    └── xlsx.py            openpyxl workbook writer
```

5 829 lines across 27 modules replace 6 257 lines across 2 — and the new total
covers more, because the customer and vendor variants that used to be separate
hand-written renderers are now views over the same column declarations.

## The table engine

`vinyllabels.reportio.table` is the abstraction the rest of the reporting code
sits on. A report is a *list of columns plus rows*; nothing else.

```python
@dataclass(frozen=True)
class Column(Generic[T]):
    header: str
    value: Callable[[T], str]
    config_key: str | None = None      # customer_report switch that reveals it
    total: Callable[[Sequence[T]], str] | None = None  # None -> blank in totals
    customer_only: bool = False        # hidden from internal output
```

`write_csv_table(...)` and `render_text_table(...)` (plus `TextColumn` for the
fixed-width ASCII tables) take that list and handle audience selection,
customer column order, alignment, separators, and the totals row. So column
order, per-row formatting, the totals behaviour, and the internal-vs-customer
subset are declared in exactly one place per column.

Two conventions the engine encodes, worth knowing before editing a report:

* A totals row sums the *displayed* quantity. Where a report has both sq-in and
  sq-ft columns, some totals add the per-row sq-ft values and others convert the
  summed sq-in; both forms exist because each keeps its total consistent with the
  column it sits in.
* Non-summable columns (ratios, unit prices, sizes) simply have no `total`, and
  render blank. `csv_reports._price_per_unit_total` exists to say that out loud
  where a per-unit figure sits next to summable money columns.

## Other decisions

* **Naming.** The tools are `generate` and `consolidate` (console scripts, plus
  `python -m vinyllabels.generate` / `python -m vinyllabels.consolidate.cli`).
  The distribution stays `onesource`; only the import package is `vinyllabels`.
* **Units.** Every measurement keeps its unit in the name (`LABEL_W_IN`,
  `total_ink_area_sq_in`) and converts once at the boundary via `units.sq_ft`.
* **Costs.** `pricing.cost_breakdown` is the only place the cost formula lives.
  Aggregations sum the components and rebuild the total from them
  (`consolidate.merge._finalize`, `metrics.calculate_metrics`) instead of summing
  each job's own total, so a headline figure cannot drift from its parts.
* **Idempotence.** The consolidated JSON carries `report_type: "consolidated"`
  and `consolidate` also skips its own output paths, so re-running over the same
  directory is safe.
* **Typing.** PEP 695 generics (`def _sum_rows[T](...)`) throughout; `mypy
  --strict` clean.

## Packaging

`pyproject.toml` gained a build system so the package is installed editable by
`uv sync`, which is what makes the console scripts work:

```toml
[project.scripts]
generate = "vinyllabels.generate:main"
consolidate = "vinyllabels.consolidate.cli:main"

[build-system]
requires = ["hatchling>=1.27.0"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/vinyllabels"]
```

`[tool.ruff] src = ["src"]` and `[tool.mypy] mypy_path = "src"` keep the
tooling pointed at the package.

## Verification

There is no test suite by design, so the migration was validated by
differential testing: copy `data/` twice, run the legacy script against one copy
and the new tool against the other with the same `--pricing-config`, then diff.

Covered: `generate` with defaults, `generate --vertical-labels`, and
`consolidate` over a directory holding both reports (all six CSV breakdowns in
every variant, both XLSX workbooks, the text reports, and the JSON).

Results:

* All CSVs, the per-job and consolidated text reports, and both XLSX workbooks
  (`xl/worksheets/sheet1.xml`) are byte-identical.
* Generated PDFs are byte-identical except for the embedded creation timestamp.
* Remaining diffs are the timestamps, the input paths echoed into the reports,
  and the three items below.

## Intentional differences

1. **Per-job PER-LABEL BREAKDOWN rows now align.** Legacy emitted money cells
   padded to the column width *after* prefixing `$`, so each row was 4 characters
   shorter than its own header and rule. The new renderer pads to the width
   including the sign.
2. **Consolidated PER-LABEL BREAKDOWN totals rule now matches.** Legacy built the
   `=` rule above the totals row from a hardcoded width list that had `Copies` at
   7 while the header rule used 8. The shared renderer derives both from the same
   columns.
3. **Global / per-PDF `total_cost` and `unit_price` differ by ~2e-16.** Legacy
   accumulated each printed instance into a running float; the new code scales a
   label's breakdown by its copy count and sums the components. Both are the same
   real number; the new order accumulates less drift. Displayed output (2-4
   decimals) is unchanged.

## Removed

`scripts/vinyl_label_prep.py`, `scripts/vinyl_label_multi_job_report.py`, and
the `main.py` stub. `scripts/count_line_lengths.py` and
`scripts/split_lines_by_length.py` are unrelated one-offs and were not touched.

Rollback: the deleted files are in git history, and the package is purely
additive on top of them — `git revert` of the removal commit restores the old
entry points without touching `src/`.
