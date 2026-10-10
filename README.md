# notebookproof

Check a notebook before you share it. Inspect saved code, run it in a fresh
kernel, compare its saved results, and check declared rules on a DataFrame.
Findings explain the evidence and a next step. The install name and import
name are both `notebookproof`.

## Install

In a notebook cell:

```python
%pip install notebookproof
```

Python 3.9 or newer is required. The base package has no required dependencies;
use the pandas or Polars DataFrame you already have in your notebook.

## Before sharing a notebook

Install the execution extra in your notebook environment:

```python
%pip install "notebookproof[verify]"
```

After upgrading an already imported version, restart the notebook kernel once.

Save the notebook, then run:

```python
# notebookproof-ignore-output
import notebookproof

report = notebookproof.check("analysis.ipynb", run=True)
```

Put the `# notebookproof-ignore-output` comment at the start of the checker
cell (before its imports if they share that cell). This explicitly excludes
the checker's own changing diagnostic output from comparison. The cell still
executes and the ignored comparison is disclosed.

The report combines static findings with fresh execution failures and per-cell
output comparisons. It identifies changed results without treating every change
as an error. Notebookproof does not write the source notebook or restart your current
kernel. It reports if the source changes or cannot be reread during verification;
it does not restore files. Fresh execution runs saved code with your usual permissions, including file writes
and network calls. Only run code you intend to execute; a fresh kernel is not a sandbox.

```python
# Details, including dependencies and the execution error when present:
report = notebookproof.check("analysis.ipynb", run=True, details=True)

# Standalone local report for reviewing or sharing:
notebookproof.save_report(report, "analysis-report.html")
```

No findings, matching outputs, and satisfied rules are evidence from specific
checks. They do not establish analytical correctness, absence of data leakage,
or reproducibility on another machine.

## Notebook preflight

Save your notebook, then run:

```python
import notebookproof

report = notebookproof.check("your-notebook.ipynb")  # Static only by default.
```

`check()` without a path attempts to discover the current document in Jupyter
or Colab. Discovery varies by host; an explicit path is the reliable option
for saved files, including VS Code. Relative paths use the kernel's working
directory. Colab can provide a live document, but its kernel state is not checked.

Preflight reports likely missing or forward references, syntax errors, saved
execution errors, missing or out-of-order execution counts, possible stale
outputs, star imports, notebook commands, and some random calls with no visible
earlier seed. Cell numbers include Markdown cells, matching the document order.
The report also lists cell reads and writes, with deferred function references
kept separate from immediate reads.

By default the display prioritises errors and warnings. Informational observations
and all cell dependencies remain in the returned dictionary and HTML report;
pass `details=True` to print them. An old saved error that does not recur in a
fresh run is labelled as historical, while a fresh execution failure is an error.

This is static analysis. It does not run your cells, validate every control-flow
path, track mutations, or inspect live kernel state. Function call timing,
conditional definitions, dynamic imports, aliases, magics, and external inputs
can require manual review. A saved execution count does not prove an output is
current, and a missing count does not prove a cell never ran.

## DataFrame health

```python
report = notebookproof.frame(df)

# Only declare a key when these columns should jointly identify one row.
report = notebookproof.frame(df, keys=["account_id", "period"])

# Optional: explicitly inspect the first N rows instead of the whole table.
report = notebookproof.frame(df, max_rows=8000)

# State what the data is supposed to satisfy; no schema is guessed.
report = notebookproof.frame(df, rules={
    "account_id": {"unique": True, "nullable": False},
    "amount": {"nullable": False, "min": 0, "max": 100000},
})
notebookproof.save_report(report, "table-report.html")
```

`frame()` checks all rows by default. It keeps the real values and null masks,
does not round-trip through CSV, and does not change the DataFrame. Literal
strings like `"NA"`, `"-"`, and `"?"` are not nulls. Integer `1` and string `"1"`
remain different values. Nullable dtypes and rows containing only nulls are
preserved. Only an eager pandas or Polars DataFrame is accepted.

Findings include missing values, mixed value types, repeated rows, declared-key
duplicates or missing key values, constant columns, numeric outliers, infinite values,
identical columns, and strong numerical correlations. A correlation is an
observation, not proof of a copied column or a mistake. Repeated observations
and nulls may be legitimate; the tool does not remove or convert anything.

Missing values and repeated rows are informational observations until a declared
rule or key makes them a violation. Rules support `nullable`, `unique`, `min`,
and `max` per column. A numeric range requires non-boolean numeric values;
comparisons keep native integer precision. Uniqueness checks exclude nulls;
use `nullable=False` if nulls are forbidden. Range limits are inclusive. Violations
include counts and the first ten affected row positions, numbered from one;
for pandas, select a reported row with `df.iloc[position - 1]`.

`rulesPassed` is `False` for confirmed violations, `True` when the declared rules
pass on inspected rows, and `None` when no rules were declared, no rows were
checked, or a rule cannot be evaluated and no violation is confirmed.
Sampling remains explicitly disclosed; passing sampled rows says nothing about
uninspected rows. These fields do not describe the quality of the entire analysis.

Sampling is opt-in and prominently disclosed. Pairwise column checks can be
expensive for very wide tables. Nested lists and dictionaries are supported;
unsupported unhashable object columns get an explicit skipped-check finding.

## Optional fresh-kernel execution

Install the execution extra:

```python
%pip install "notebookproof[verify]"
```

Then, from a notebook cell:

```python
result = notebookproof.verify("your-notebook.ipynb", timeout=60)
```

This uses Jupyter's NBClient to run saved code in a separate, fresh kernel.
It reports success or the first failure and cell number, including timeouts,
plus per-cell output comparisons.
The timeout is per cell. The original notebook file is not written, and your
current notebook kernel is not restarted. The saved kernelspec is used by
default; pass `kernel_name="python3"` to choose another installed kernel.

Execution uses your normal permissions. Notebook code can write files, contact
services, or perform other side effects; only run notebooks you intend to
execute. A fresh kernel is not a sandbox. Nested `verify()` calls are skipped
to prevent recursive kernel creation.

Comparison checks saved text, HTML, Markdown, LaTeX, JSON, PNG, JPEG and SVG
payloads. Execution counts, display metadata and transient identifiers are
excluded; stream chunks are combined without changing their text. JSON value
types remain distinct. Images are compared by payload, not by visual similarity.
Widgets and unrecognised MIME types are disclosed as unsupported.

| Cell status | Meaning |
|---|---|
| `matched` | The supported saved and fresh payloads match. |
| `changed` | Supported payloads differ; review whether this is expected. |
| `no_baseline` | No saved execution count or output provides a baseline. |
| `partial` / `unsupported` | Some or all output formats were not compared. |
| `failed` / `not_run` | Execution failed here or did not reach this cell. |
| `skipped` | The cell has the Jupyter execution tag `skip-execution`. |
| `ignored` | Its code ran, but comparison was explicitly disabled. |

`outputsMatch` is `False` when a supported result changes, `True` only when
all executable cells have comparable matching baselines, and otherwise `None`.
`comparisonComplete` discloses coverage independently of execution success.
Matching empty outputs does not inspect variables that were never displayed.
Timestamps, random draws, numeric formatting, HTML identifiers and image encoding
can cause legitimate changes; comparison does not infer whether the analysis is wrong.

Use the cell tag `notebookproof-ignore-output` or the first-line comment
`# notebookproof-ignore-output` for deliberately variable diagnostic output.
It does not skip execution or hide the comparison gap. Pass
`compare_outputs=False` to `verify()` to perform execution alone.

The standalone HTML report has no remote assets or scripts and omits raw outputs,
source code and example data values. It retains finding descriptions, variable
and column names; review it before sharing. Existing files are preserved by default.
Pass `overwrite=True` to `save_report()` only to intentionally replace an HTML report.
Notebookproof's own HTML exports are skipped inside fresh verification, so the
checker cannot replace its existing report or fail merely because that report exists.
Keep diagnostic cells marked for ignored output comparison; the gap remains visible.

## Try it and give feedback

Open [the five-minute trial notebook](https://github.com/scottcampbelldata/notebookproof/blob/main/examples/quickstart.ipynb) in the notebook
environment you already use. The [trial questions](https://github.com/scottcampbelldata/notebookproof/blob/main/docs/user-trial.md) cover
whether the first useful finding arrives quickly and whether you would use
the checker again. Adoption has not yet been measured.

## Development and publishing

```console
python -m pip install -e ".[test]" build twine
python -m pytest -q
python -m build
python -m twine check dist/*
```

GitHub Actions checks Windows and Linux on Python 3.9 and 3.14.
[Publishing setup](https://github.com/scottcampbelldata/notebookproof/blob/main/docs/publishing.md) describes the PyPI trusted publisher;
no long-lived PyPI upload token is required.

Version 0.3 adds full checks, output comparison, declared table rules and local HTML
reports. Existing `check(path)` remains static and `frame(df)` keeps its values.
Version 0.2 removed the old `score` and `verdict` fields. Use individual findings,
their severity, and the disclosed inspection limits. The legacy CSV helpers
remain available but preserve strings rather than guessing data types or nulls.
