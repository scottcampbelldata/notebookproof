"""Notebook preflight and DataFrame health checks, inside your notebook.

check() inspects saved code. It does not execute cells or prove reproducibility.
frame(df) inspects actual pandas or Polars values without changing them.
"""
from __future__ import annotations

import ast
import builtins
import csv
import io
import json
import math
import os
import statistics
import sys
import symtable
from datetime import date, datetime
from decimal import Decimal
from numbers import Real
from pathlib import Path

__version__ = "0.2.1"
AMBIENT = set(dir(builtins)) | {"get_ipython", "display", "__name__", "__file__", "In", "Out"}


def check(path=None):
    """Inspect notebook code and metadata; no notebook cells are executed."""
    nb, where = _load_notebook(path)
    report = analyze_notebook(nb)
    report["where"] = where
    _print_notebook(report)
    return report


def notebook(path):
    """Alias for check(path)."""
    return check(path)


def verify(path, *, timeout=60, kernel_name=None):
    """Execute saved code in a fresh kernel, without writing the notebook.

    This runs the notebook's side effects with your normal permissions: it is
    not a sandbox. timeout is per cell. Outputs are not compared with old ones.
    Install notebookproof[verify] to enable this optional check.
    """
    if type(timeout) is not int or timeout < 1:
        raise ValueError("timeout must be a positive integer in seconds.")
    if os.environ.get("NOTEBOOKPROOF_VERIFY_ACTIVE") == "1":
        print("Nested verify() skipped: a fresh-kernel check is already running.")
        return dict(executed=False, skipped=True, outputsCompared=False)
    file = Path(path).expanduser().resolve()
    if not file.is_file():
        raise FileNotFoundError(f"No notebook at {file}.")
    try:
        import nbformat
        from nbclient import NotebookClient
    except ImportError as exc:
        raise ImportError("Install notebookproof[verify] to run a fresh-kernel check.") from exc
    nb = nbformat.reads(file.read_text(encoding="utf-8-sig"), as_version=4)
    selected = kernel_name or nb.metadata.get("kernelspec", {}).get("name") or "python3"
    current = None

    def starting(*, cell, cell_index):
        nonlocal current
        current = cell_index + 1

    client = NotebookClient(nb, timeout=timeout, kernel_name=selected,
        force_raise_errors=True, resources={"metadata": {"path": str(file.parent)}},
        on_cell_start=starting)
    error = None
    try:
        client.execute(env=dict(os.environ, NOTEBOOKPROOF_VERIFY_ACTIVE="1"))
    except Exception as exc:
        error = exc
    report = dict(executed=current is not None, succeeded=error is None,
        outputsCompared=False, kernelName=selected, failedCell=current if error else None,
        errorName=getattr(error, "ename", type(error).__name__) if error else None,
        error=str(error)[:2000] if error else None,
        limitations=["Output equivalence and reproducibility were not verified.",
            "Code runs with normal permissions and may modify files or call external services.",
            "Nested verify() calls are skipped to prevent recursive kernel creation."])
    if error:
        print(f"Fresh-kernel execution failed at cell {current or '?'}: {report['errorName']}")
        print(report["error"])
    else:
        print(f"Fresh-kernel execution completed using {selected}.")
    print("Original notebook was not written. Output equivalence was not compared.")
    return report


def _finding(severity, code, title, detail, **where):
    return dict(severity=severity, code=code, title=title, detail=detail, **where)


def analyze_notebook(nb):
    """Static findings and dependencies, with explicit inspection limits."""
    if not isinstance(nb, dict) or not isinstance(nb.get("cells"), list):
        raise ValueError("Expected a .ipynb notebook with a cells list.")
    cells, findings, trees = [], [], []
    dynamic, postponed = False, sys.version_info >= (3, 14)
    for index, cell in enumerate(nb["cells"]):
        if not isinstance(cell, dict):
            raise ValueError(f"Notebook cell {index + 1} must be an object.")
        if cell.get("cell_type") != "code":
            continue
        source = _cell_source(cell)
        count = cell.get("execution_count")
        symbols = {"defines": [], "uses": [], "deferred": []}
        tree = None
        try:
            python_source, magic = _python_source(source)
            tree = ast.parse(python_source)
            postponed |= any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
                             and any(a.name == "annotations" for a in n.names) for n in tree.body)
            symbols = cell_symbols(python_source, postponed_annotations=postponed)
            dynamic |= any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                           and n.func.id in {"exec", "eval"} for n in ast.walk(tree))
            if magic:
                dynamic = True
                findings.append(_finding("info", "MAGIC", "Notebook commands need manual review",
                    "IPython commands can create names or run other code. Their effects are not verified here.", cell=index))
            if any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names) for n in ast.walk(tree)):
                dynamic = True
                findings.append(_finding("warn", "STAR_IMPORT", "Star import hides name origins",
                    "Import names explicitly so dependencies can be checked.", cell=index))
        except (SyntaxError, ValueError) as exc:
            findings.append(_finding("error", "SYNTAX_ERROR", "Python could not parse this cell",
                f"{exc}. Check the code and this kernel's Python version.", cell=index))
        for output in cell.get("outputs", []):
            if isinstance(output, dict) and output.get("output_type") == "error":
                findings.append(_finding("error", "SAVED_ERROR", "An execution error is saved here",
                    f"{output.get('ename', 'Error')}: {output.get('evalue', '')}. Fix and rerun; this may be an older output.", cell=index))
        cells.append(dict(index=index, codeIndex=len(cells), source=source,
            preview=_preview(source), executionCount=count if type(count) is int else None,
            defines=symbols["defines"], uses=symbols["uses"], deferred=symbols["deferred"], reads=[], stale=False))
        trees.append(tree)
    owners = {}
    for cell in cells:
        for name in cell["defines"]:
            owners.setdefault(name, []).append(cell["index"])
    available = set(AMBIENT)
    for cell in cells:
        for name in cell["uses"]:
            prior = [i for i in owners.get(name, []) if i < cell["index"]]
            later = [i for i in owners.get(name, []) if i > cell["index"]]
            cell["reads"].append({"name": name, "from": prior[-1] if prior else later[0] if later else None})
            if name in available:
                continue
            detail = (f"{name} is assigned later, in cell {later[0] + 1}." if later
                      else f"No earlier visible assignment or import of {name} was found.")
            findings.append(_finding("info" if dynamic else "warn", "FORWARD_REF" if later else "UNDEFINED",
                f"Check where {name} comes from", detail + " Put required setup before this use; dynamic code may supply it.", cell=cell["index"]))
        for name in cell["deferred"]:
            cell["reads"].append({"name": name, "from": (owners.get(name) or [None])[0], "deferred": True})
            if name not in owners and name not in AMBIENT:
                findings.append(_finding("info" if dynamic else "warn", "UNDEFINED", f"Check the deferred reference to {name}",
                    "A function or generator references this global name. No notebook definition was found; it is looked up when called.", cell=cell["index"]))
        available.update(cell["defines"])
    ran = [c for c in cells if c["executionCount"] is not None]
    if any(b["executionCount"] < a["executionCount"] for a, b in zip(ran, ran[1:])):
        findings.append(_finding("warn", "OUT_OF_ORDER", "Saved execution counts are out of visual order",
            "Review dependencies or restart and run all when appropriate; counts alone do not prove stale state."))
    unrun = [c for c in cells if c["executionCount"] is None and c["source"].strip()]
    if unrun:
        findings.append(_finding("info", "PARTIAL_RUN" if ran else "NEVER_RUN", "Some cells have no saved execution count",
            "Cells " + ", ".join(str(c["index"] + 1) for c in unrun) + ". They may be unrun, or their metadata may have been cleared."))
    # ponytail: counts suggest possible stale output, not runtime provenance; runtime tracking is needed for live state.
    by_index = {c["index"]: c for c in cells}
    for cell in ran:
        for read in cell["reads"]:
            if read.get("deferred"):
                continue
            parent = by_index.get(read["from"])
            if parent and parent["executionCount"] is not None and parent["executionCount"] > cell["executionCount"]:
                cell["stale"] = True
                findings.append(_finding("warn", "POSSIBLY_STALE", f"Review output using {read['name']}",
                    f"Cell {parent['index'] + 1}, which defines this name, has a later saved execution count. Rerun if that input changed.", cell=cell["index"]))
    findings.extend(_random_findings(cells, trees))
    findings.sort(key=lambda f: ({"error": 0, "warn": 1, "info": 2}[f["severity"]], f.get("cell", -1)))
    return dict(codeCells=len(cells), executed=False,
        headline="Static preflight only; execution and reproducibility are not verified.", findings=findings, cells=cells,
        limitations=["Reads saved code and metadata, except Colab's live document when available.",
            "Does not validate control flow, function call timing, external files, side effects, or live kernel state.",
            "Dynamic names, magics, conditional definitions and mutations may need manual review."])


def _bound_names(node):
    names = set()

    class Bindings(ast.NodeVisitor):
        def visit_Name(self, item):
            if isinstance(item.ctx, ast.Store):
                names.add(item.id)

        def visit_FunctionDef(self, item):
            names.add(item.name)

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef

        def visit_Import(self, item):
            names.update(a.asname or a.name.split(".")[0] for a in item.names)

        def visit_ImportFrom(self, item):
            if item.module != "__future__":
                names.update(a.asname or a.name for a in item.names if a.name != "*")

        def visit_Lambda(self, item):
            pass

        def visit_ListComp(self, item):
            names.update(n.target.id for n in ast.walk(item) if isinstance(n, ast.NamedExpr))

        visit_SetComp = visit_ListComp
        visit_DictComp = visit_ListComp
        visit_GeneratorExp = visit_ListComp

        def visit_ExceptHandler(self, item):
            if item.name:
                names.add(item.name)
            self.generic_visit(item)

    Bindings().visit(node)
    return names


class _ImmediateReads(ast.NodeVisitor):
    """Immediate AST reads; Python's symtable handles deferred scopes."""
    def __init__(self, postponed_annotations=False):
        self.scopes = [set()]
        self.uses = set()
        self.postponed_annotations = postponed_annotations

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load) and not any(node.id in s for s in self.scopes):
            self.uses.add(node.id)

    def statements(self, statements):
        for statement in statements:
            self.visit(statement)
            self.scopes[-1].update(_bound_names(statement))

    def visit_FunctionDef(self, node):
        for expression in node.decorator_list + node.args.defaults + [v for v in node.args.kw_defaults if v is not None]:
            self.visit(expression)
        args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs + [a for a in (node.args.vararg, node.args.kwarg) if a]
        for arg in args:
            if arg.annotation and not self.postponed_annotations:
                self.visit(arg.annotation)
        if node.returns and not self.postponed_annotations:
            self.visit(node.returns)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        for expression in node.args.defaults + [v for v in node.args.kw_defaults if v is not None]:
            self.visit(expression)

    def visit_ClassDef(self, node):
        for expression in node.decorator_list + node.bases + [k.value for k in node.keywords]:
            self.visit(expression)
        self.scopes.append(set())
        self.statements(node.body)
        self.scopes.pop()

    def visit_ListComp(self, node):
        self.scopes.append(set())
        for generator in node.generators:
            self.visit(generator.iter)
            self.scopes[-1].update(_bound_names(generator.target))
            for condition in generator.ifs:
                self.visit(condition)
        for expression in ([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]):
            self.visit(expression)
        self.scopes.pop()

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp

    def visit_GeneratorExp(self, node):
        self.visit(node.generators[0].iter)

    def visit_If(self, node):
        self.visit(node.test)
        self.statements(node.body)
        self.statements(node.orelse)

    visit_While = visit_If

    def visit_Try(self, node):
        self.statements(node.body)
        for handler in node.handlers:
            self.visit(handler)
        self.statements(node.orelse)
        self.statements(node.finalbody)

    def visit_AugAssign(self, node):
        if isinstance(node.target, ast.Name):
            self.visit_Name(ast.Name(id=node.target.id, ctx=ast.Load()))
        else:
            self.visit(node.target)
        self.visit(node.value)

    def visit_For(self, node):
        self.visit(node.iter)
        self.scopes[-1].update(_bound_names(node.target))
        self.statements(node.body)
        self.statements(node.orelse)

    visit_AsyncFor = visit_For

    def visit_With(self, node):
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars:
                self.scopes[-1].update(_bound_names(item.optional_vars))
        self.statements(node.body)

    visit_AsyncWith = visit_With

    def visit_ExceptHandler(self, node):
        if node.type:
            self.visit(node.type)
        if node.name:
            self.scopes[-1].add(node.name)
        self.statements(node.body)


def cell_symbols(source, *, postponed_annotations=False):
    source, _ = _python_source(source)
    tree = ast.parse(source)
    postponed = postponed_annotations or any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
                    and any(a.name == "annotations" for a in n.names) for n in tree.body)
    reader = _ImmediateReads(postponed_annotations=postponed)
    reader.statements(tree.body)
    deferred = set()

    def globals_in(table, is_deferred=False):
        deferred_here = is_deferred or (table.get_type() == "function" and table.get_name() not in {"listcomp", "setcomp", "dictcomp"})
        if deferred_here:
            deferred.update(s.get_name() for s in table.get_symbols() if s.is_global() and s.is_referenced())
        for child in table.get_children():
            globals_in(child, deferred_here)

    globals_in(symtable.symtable(source, "<notebook-cell>", "exec"))
    return dict(defines=sorted(_bound_names(tree)), uses=sorted(reader.uses - AMBIENT), deferred=sorted(deferred - AMBIENT))


def _python_source(source):
    try:
        ast.parse(source)
        return source, False
    except SyntaxError:
        pass
    magic = any(line.lstrip().startswith(("%", "!")) or " = %" in line or " = !" in line for line in source.splitlines())
    if not magic:
        return source, False
    try:
        from IPython.core.inputtransformer2 import TransformerManager
        return TransformerManager().transform_cell(source), True
    except ImportError:
        if source.lstrip().startswith("%%"):
            return "", True
        return "\n".join("pass" if line.lstrip().startswith(("%", "!")) else line for line in source.splitlines()), True


def _random_findings(cells, trees):
    aliases, seeded, findings = {}, set(), []
    for cell, tree in zip(cells, trees):
        if tree is None:
            continue
        for node in sorted(ast.walk(tree), key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0))):
            if isinstance(node, ast.Import):
                aliases.update((a.asname or a.name.split(".")[0], a.name if a.asname else a.name.split(".")[0]) for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                aliases.update((a.asname or a.name, f"{node.module}.{a.name}") for a in node.names)
            elif isinstance(node, ast.Call):
                root, *rest = ast.unparse(node.func).split(".")
                name = ".".join([aliases.get(root, root)] + rest)
                explicit = any(k.arg in {"seed", "random_state"} and not (isinstance(k.value, ast.Constant) and k.value.value is None) for k in node.keywords)
                arg_seed = node.args and not (isinstance(node.args[0], ast.Constant) and node.args[0].value is None)
                if name.endswith((".seed", ".manual_seed", ".set_seed")) and arg_seed:
                    seeded.add(name.rsplit(".", 1)[0])
                    continue
                if name in {"numpy.random.default_rng", "numpy.random.RandomState"} and (explicit or arg_seed):
                    continue
                if name.startswith(("numpy.random.", "random.", "torch.rand", "tensorflow.random.")) and not explicit and not any(name.startswith(s + ".") for s in seeded):
                    findings.append(_finding("warn", "UNSEEDED_RANDOM", "Review a random call without a visible earlier seed",
                        f"{name} has no seed recognised here. Set the appropriate seed before the draw when repeatability matters; control flow and aliases need manual review.", cell=cell["index"]))
    return findings


def frame(df, *, keys=None, max_rows=None):
    """Inspect all rows by default; keys declares a unique, possibly composite key.

    max_rows limits inspection to the first N rows and is disclosed in the output.
    No dataframe values, index, or dtypes are changed.
    """
    if max_rows is not None and (type(max_rows) is not int or max_rows < 1):
        raise ValueError("max_rows must be a positive integer or None.")
    library = type(df).__module__.split(".")[0]
    if library == "pandas":
        import pandas as pd
        if not isinstance(df, pd.DataFrame):
            raise TypeError("Pass a pandas or Polars DataFrame, not a Series.")
        total = len(df)
        sample = df.iloc[:max_rows] if max_rows is not None else df
        headers = list(sample.columns)
        values = [sample.iloc[:, i].tolist() for i in range(len(headers))]
        masks = [sample.iloc[:, i].isna().tolist() for i in range(len(headers))]
        dtypes = [str(dtype) for dtype in sample.dtypes]
    elif library == "polars":
        import polars as pl
        if not isinstance(df, pl.DataFrame):
            raise TypeError("Pass an eager pandas or Polars DataFrame.")
        total = df.height
        sample = df.head(max_rows) if max_rows is not None else df
        headers = list(sample.columns)
        values = [sample.get_column(h).to_list() for h in headers]
        masks = [[v is None or (isinstance(v, float) and math.isnan(v)) for v in col] for col in values]
        dtypes = [str(dtype) for dtype in sample.dtypes]
    else:
        raise TypeError("Pass a pandas or Polars DataFrame.")
    if len(set(headers)) != len(headers):
        raise ValueError("Column labels must be unique; rename duplicate columns before checking.")
    key_names = [keys] if isinstance(keys, str) else list(keys) if keys is not None else []
    if any(k not in headers for k in key_names):
        raise ValueError("Every key column must exist in the DataFrame.")
    report = _analyze_columns(headers, values, masks, len(sample), keys=key_names, dtypes=dtypes)
    report.update(totalRows=total, truncated=len(sample) < total)
    _print_table(report)
    return report


def _value_key(value):
    if isinstance(value, dict):
        return (dict, frozenset((_value_key(k), _value_key(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return (type(value), tuple(_value_key(v) for v in value))
    if isinstance(value, (set, frozenset)):
        return (type(value), frozenset(_value_key(v) for v in value))
    return (type(value), value)


def _kind(value):
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (Real, Decimal)):
        return "number"
    if isinstance(value, (date, datetime)):
        return "date"
    if isinstance(value, str):
        return "text"
    return type(value).__name__


def _analyze_columns(headers, values, masks, nrows, *, keys=(), dtypes=None):
    columns, findings, keyed = [], [], []
    if not headers or not nrows:
        findings.append(_finding("warn", "EMPTY", "The DataFrame has no rows or no columns", "Load the intended data before assessing its health."))
    for i, name in enumerate(headers):
        present = [v for v, missing in zip(values[i], masks[i]) if not missing]
        kinds = {_kind(v) for v in present}
        kind = next(iter(kinds)) if len(kinds) == 1 else "mixed" if kinds else "empty"
        try:
            typed = [_value_key(v) if not missing else None for v, missing in zip(values[i], masks[i])]
            unique = len(set(k for k in typed if k is not None))
        except (TypeError, ValueError):
            unique, typed = None, None
            findings.append(_finding("info", "CHECK_SKIPPED", f"Some uniqueness checks for {name} were skipped",
                "Unsupported unhashable objects were found. Convert to ordinary scalar values if uniqueness matters.", column=name))
        keyed.append(typed)
        missing = sum(masks[i])
        numeric = None
        if kind == "number":
            finite = [float(v) for v in present if math.isfinite(float(v))]
            if len(finite) != len(present):
                findings.append(_finding("warn", "NONFINITE", f"{name} contains infinite values", "Review the calculation that produced infinity before using statistics.", column=name))
            if finite:
                scale = max(map(abs, finite)) or 1.0
                scaled = [v / scale for v in finite]
                numeric = dict(min=min(finite), max=max(finite),
                    mean=statistics.fmean(scaled) * scale, median=statistics.median(scaled) * scale)
                if len(scaled) >= 4:
                    q1, _, q3 = statistics.quantiles(scaled, n=4, method="inclusive")
                    iqr = q3 - q1
                    outlier = sum(v < q1 - 1.5 * iqr or v > q3 + 1.5 * iqr for v in scaled)
                    numeric["outlier"] = outlier
                    if outlier:
                        findings.append(_finding("info", "OUTLIERS", f"{name} has {outlier} values outside the usual spread",
                            "Outside the 1.5-IQR limits. Review these observations; they may be legitimate and are not removed.", column=name))
        columns.append(dict(name=name, dtype=dtypes[i] if dtypes else None, kind=kind,
            missing=missing, missingRate=missing / nrows if nrows else 0, unique=unique,
            constant=unique == 1, examples=[repr(v)[:80] for v in present[:3]], numeric=numeric))
        if nrows and missing:
            findings.append(_finding("warn", "EMPTY_COL" if missing == nrows else "MISSING", f"{name} has {missing} missing values",
                f"{missing / nrows:.1%} of checked rows. Decide whether nulls are expected; literal strings such as 'NA' are not counted as nulls.", column=name))
        if kind == "mixed":
            findings.append(_finding("warn", "MIXED", f"{name} contains different value types",
                "Types: " + ", ".join(sorted(kinds)) + ". Check whether this is intentional before converting.", column=name))
        if unique == 1 and nrows > 1:
            findings.append(_finding("info", "CONSTANT", f"{name} has one distinct non-null value",
                "This can be intentional; check the import or filter if variation was expected.", column=name))
    duplicates = 0 if all(v is not None for v in keyed) else None
    if headers and all(v is not None for v in keyed):
        duplicates = nrows - len(set(zip(*keyed)))
        if duplicates:
            findings.append(_finding("warn", "DUP_ROWS", f"{duplicates} repeated rows beyond the first occurrence",
                "Decide whether repeated observations are legitimate before removing rows."))
    if keys:
        indexes = [headers.index(k) for k in keys]
        if all(keyed[i] is not None for i in indexes):
            key_rows = list(zip(*(keyed[i] for i in indexes)))
            null_keys = sum(any(k is None for k in row) for row in key_rows)
            extra = len(key_rows) - len(set(key_rows))
            if null_keys:
                findings.append(_finding("warn", "MISSING_KEY", f"{null_keys} rows have an incomplete declared key",
                    "Declared key: " + ", ".join(map(str, keys)) + ". Review nulls in these columns."))
            if extra:
                findings.append(_finding("warn", "DUP_KEY", f"{extra} repeated declared keys beyond the first occurrence",
                    "Declared key: " + ", ".join(map(str, keys)) + ". Check the intended grain of the table."))
    # ponytail: column pairs cost O(rows * columns^2); restrict pairs if very wide tables become a real workload.
    for a in range(len(headers)):
        for b in range(a + 1, len(headers)):
            if nrows and keyed[a] is not None and keyed[a] == keyed[b]:
                findings.append(_finding("info", "COPY_COLUMN", f"{headers[a]} and {headers[b]} have identical checked values",
                    "Values, types, and null positions match. Confirm whether both columns are intended."))
            elif columns[a]["kind"] == columns[b]["kind"] == "number":
                pairs = [(float(x), float(y)) for x, y, mx, my in zip(values[a], values[b], masks[a], masks[b])
                         if not mx and not my and math.isfinite(float(x)) and math.isfinite(float(y))]
                r = _pearson([x for x, _ in pairs], [y for _, y in pairs])
                if r is not None and abs(r) >= .9:
                    findings.append(_finding("info", "CORRELATED", f"{headers[a]} and {headers[b]} are strongly correlated",
                        f"Pearson correlation {r:.2f} across {len(pairs)} paired values. Correlation does not establish a copy or an error."))
    return dict(rows=nrows, cols=len(headers), duplicateRows=duplicates, findings=findings, columns=columns,
        headline="DataFrame observations only; whether they are problems depends on the intended use.")


def parse_table(text):
    """Legacy CSV/TSV helper. Strings remain strings; no record limit is applied."""
    if not isinstance(text, str):
        raise TypeError("Expected CSV or TSV text.")
    if not text.strip():
        return [], [], False
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    records = list(csv.reader(io.StringIO(text.lstrip("\ufeff")), dialect))
    headers = records[0]
    if len(set(headers)) != len(headers):
        raise ValueError("Column labels must be unique.")
    rows = [r for r in records[1:] if r]
    if any(len(r) != len(headers) for r in rows):
        raise ValueError("Every CSV row must have the same number of fields as the header.")
    return headers, rows, False


def analyze_table(headers, rows, truncated=False):
    """Legacy record helper; no type coercion or special null-token guessing."""
    if any(len(row) != len(headers) for row in rows):
        raise ValueError("Row widths must match headers.")
    values = [[r[i] for r in rows] for i in range(len(headers))]
    report = _analyze_columns(headers, values, [[v is None for v in col] for col in values], len(rows))
    report.update(totalRows=None if truncated else len(rows), truncated=bool(truncated))
    return report


def _print_findings(report):
    if not report["findings"]:
        print("No findings from the checks performed.")
    for f in report["findings"]:
        where = f"  cell {f['cell'] + 1}" if "cell" in f else f"  {f['column']}" if "column" in f else ""
        print(f"{f['severity'].upper():5}  {f['code']}{where}: {f['title']}")
        print(f"       {f['detail']}")


def _print_notebook(report):
    print(f"notebookproof: {report['codeCells']} code cells; {len(report['findings'])} findings")
    print(report.get("where", ""))
    print(report["headline"])
    _print_findings(report)
    for cell in report["cells"]:
        print(f"cell {cell['index'] + 1}: writes {', '.join(cell['defines']) or '-'}; reads {', '.join(cell['uses'] + cell['deferred']) or '-'}")


def _print_table(report):
    print(f"notebookproof: {report['rows']} checked rows x {report['cols']} columns; {len(report['findings'])} findings")
    if report["truncated"]:
        print(f"Sample only: first {report['rows']} of {report['totalRows']} rows. Later rows were not checked.")
    print(report["headline"])
    _print_findings(report)


def _load_notebook(path):
    if path is not None:
        file = Path(path).expanduser()
        if not file.is_file():
            raise FileNotFoundError(f"No notebook at {file}. Save it, then pass the file name.")
        return json.loads(file.read_text(encoding="utf-8-sig")), f"Read {file.resolve()}. Save first if you just edited it."
    live = _colab_notebook()
    if live is not None:
        return live, "Read the live Colab document; execution and live kernel state were not verified."
    found = _jupyter_notebook_path()
    if found is None:
        raise RuntimeError("The notebook path could not be discovered. Save it, then call notebookproof.check('your-notebook.ipynb'). frame(df) does not need a file.")
    file = Path(found)
    return json.loads(file.read_text(encoding="utf-8-sig")), f"Read {file.resolve()}. Save first if you just edited it."


def _colab_notebook():
    try:
        from google.colab import _message
        reply = _message.blocking_request("get_ipynb", timeout_sec=8)
    except Exception:
        return None
    nb = reply.get("ipynb") if isinstance(reply, dict) else None
    return nb if isinstance(nb, dict) and isinstance(nb.get("cells"), list) else None


def _jupyter_notebook_path():
    try:
        import ipynbname
        return str(ipynbname.path())
    except Exception:
        pass
    try:
        from ipykernel.connect import get_connection_file
        from jupyter_server.serverapp import list_running_servers
        stem = Path(get_connection_file()).stem
    except Exception:
        return None
    if not stem.startswith("kernel-"):
        return None
    import urllib.request
    for server in list_running_servers():
        url = str(server.get("url", "")).rstrip("/")
        if not url:
            continue
        req = urllib.request.Request(f"{url}/api/sessions")
        if server.get("token"):
            req.add_header("Authorization", f"token {server['token']}")
        try:
            with urllib.request.urlopen(req, timeout=2) as response:
                sessions = json.loads(response.read().decode())
        except Exception:
            continue
        for session in sessions:
            if (session.get("kernel") or {}).get("id") == stem[len("kernel-"):]:
                path = session.get("path") or (session.get("notebook") or {}).get("path")
                if path:
                    return str(Path(server.get("root_dir") or server.get("notebook_dir") or ".") / path)
    return None


def _cell_source(cell):
    source = cell.get("source") or ""
    return "".join(source) if isinstance(source, list) else str(source)


def _preview(source):
    return next((line.strip()[:88] for line in source.splitlines() if line.strip() and not line.lstrip().startswith("#")), "(empty)")


def _pearson(xs, ys):
    if len(xs) < 4 or min(xs) == max(xs) or min(ys) == max(ys):
        return None
    # Scale before centering to avoid overflow for large finite values.
    sx, sy = max(map(abs, xs)), max(map(abs, ys))
    xs, ys = [x / sx for x in xs], [y / sy for y in ys]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    dx, dy = [x - mx for x in xs], [y - my for y in ys]
    denom = math.sqrt(sum(x * x for x in dx) * sum(y * y for y in dy))
    return sum(x * y for x, y in zip(dx, dy)) / denom if denom else None
