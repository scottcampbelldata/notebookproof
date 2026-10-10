# notebookproof

Notebook preflight and DataFrame health checks that run inside your notebook.
Findings explain the evidence and suggest a next step. No scores or guarantees
of reproducibility. The install name and import name are both `notebookproof`.

## Install

In a notebook cell:

```python
%pip install notebookproof
```

Python 3.9 or newer is required. The base package has no required dependencies;
use the pandas or Polars DataFrame you already have in your notebook.

## Notebook preflight

Save your notebook, then run:

```python
import notebookproof

report = notebookproof.check("your-notebook.ipynb")
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
It reports success or the first failure and cell number, including timeouts.
The timeout is per cell. The original notebook file is not written, and your
current notebook kernel is not restarted. The saved kernelspec is used by
default; pass `kernel_name="python3"` to choose another installed kernel.

Execution uses your normal permissions. Notebook code can write files, contact
services, or perform other side effects; only run notebooks you intend to
execute. A fresh kernel is not a sandbox. Nested `verify()` calls are skipped
to prevent recursive kernel creation. Successful execution does not establish
that old and new outputs match; output equivalence is not compared.

## Try it and give feedback

Open [the five-minute trial notebook](examples/quickstart.ipynb) in the notebook
environment you already use. The [trial questions](docs/user-trial.md) cover
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
[Publishing setup](docs/publishing.md) describes the PyPI trusted publisher;
no long-lived PyPI upload token is required.

Version 0.2 removes the old `score` and `verdict` fields. Use individual findings,
their severity, and the disclosed inspection limits. The legacy CSV helpers
remain available but preserve strings rather than guessing data types or nulls.
