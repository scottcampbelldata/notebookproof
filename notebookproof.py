"""Notebook preflight and DataFrame health checks, inside your notebook.

check() inspects saved code. It does not execute cells or prove reproducibility.
frame(df) inspects actual pandas or Polars values without changing them.
"""
from __future__ import annotations

import ast
import builtins
import csv
import copy
import html
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

__version__ = "0.3.0"
AMBIENT = set(dir(builtins)) | {"get_ipython", "display", "__name__", "__file__", "In", "Out"}


def check(path=None, *, run=False, timeout=60, kernel_name=None, details=False):
    """Preflight saved code; run=True also executes and compares in a fresh kernel."""
    if type(run) is not bool:
        raise ValueError("run must be True or False.")
    nb, where, file = _load_notebook(path)
    report = analyze_notebook(nb)
    report["where"] = where
    if run:
        if file is None:
            raise ValueError("Save a local .ipynb and pass its path for a fresh-kernel check.")
        execution = verify(file, timeout=timeout, kernel_name=kernel_name, details=details)
        report["execution"] = execution
        report["executed"] = execution["executed"]
        report["headline"] = "Preflight and fresh-run evidence; review changes and inspection gaps below."
        if not execution.get('skipped') and execution['sourceStatus'] != 'unchanged':
            report['findings'].append(_finding('error', 'SOURCE_CHANGED', 'Source notebook changed or could not be reread',
                'Results refer to the saved snapshot. Inspect the current file; no restoration was attempted.'))
        finished = {c['cell'] - 1 for c in execution['outputComparisons']
                    if c['status'] not in {'failed', 'not_run', 'skipped'}}
        for finding in report['findings']:
            if finding['code'] == 'SAVED_ERROR' and finding.get('cell') in finished:
                finding.update(severity='info', title='An old saved error did not recur',
                    detail='This cell completed in the fresh run. Its saved error belongs to an earlier run.')
            elif finding['code'] == 'SYNTAX_ERROR' and finding.get('cell') in finished:
                finding.update(severity='info', title='Static parsing failed, but fresh execution completed',
                    detail='The static parser could not inspect this cell. The selected kernel accepted it; check parser/kernel version differences and notebook commands.')
        if not execution.get('skipped') and not execution['succeeded']:
            cell = execution['failedCell']
            report['findings'].append(_finding('error', 'FRESH_RUN_FAILED',
                f"Fresh execution failed: {execution['errorName']}",
                'Review this cell and its required inputs or environment. Use details=True for the execution error.',
                **({'cell': cell - 1} if cell else {})))
        for comparison in execution['outputComparisons']:
            if comparison['status'] == 'changed':
                report['findings'].append(_finding('warn', 'OUTPUT_CHANGED', 'Fresh output differs from the saved result',
                    comparison['detail'], cell=comparison['cell'] - 1))
        if not execution['comparisonComplete']:
            report['findings'].append(_finding('info', 'COMPARISON_GAPS', 'Some results were not fully compared',
                'See the per-cell comparison: missing baselines, unsupported output types, ignored cells or execution failures are disclosed.'))
        report['findings'].sort(key=lambda f: ({'error': 0, 'warn': 1, 'info': 2}[f['severity']], f.get('cell', -1)))
    _print_notebook(report, details=details)
    return report


def notebook(path):
    """Alias for check(path)."""
    return check(path)


def verify(path, *, timeout=60, kernel_name=None, compare_outputs=True, details=False):
    """Execute saved code in a fresh kernel, without writing the notebook.

    This runs the notebook's side effects with your normal permissions: it is
    not a sandbox. timeout is per cell. Supported saved output payloads are compared,
    without execution counts or display metadata. This does not prove correctness.
    Install notebookproof[verify] to enable this optional check.
    """
    if type(timeout) is not int or timeout < 1:
        raise ValueError("timeout must be a positive integer in seconds.")
    if type(compare_outputs) is not bool:
        raise ValueError("compare_outputs must be True or False.")
    if os.environ.get("NOTEBOOKPROOF_VERIFY_ACTIVE") == "1":
        print("Nested verify() skipped: a fresh-kernel check is already running.")
        return dict(executed=False, skipped=True, succeeded=None, outputsCompared=False,
                    comparisonComplete=False, outputsMatch=None, outputComparisons=[])
    file = Path(path).expanduser().resolve()
    if not file.is_file():
        raise FileNotFoundError(f"No notebook at {file}.")
    try:
        import nbformat
        from nbclient import NotebookClient
    except ImportError as exc:
        raise ImportError("Install notebookproof[verify] to run a fresh-kernel check.") from exc
    source_bytes = file.read_bytes()
    nb = nbformat.reads(source_bytes.decode("utf-8-sig"), as_version=4)
    before = copy.deepcopy(nb)
    selected = kernel_name or nb.metadata.get("kernelspec", {}).get("name") or "python3"
    current, started = None, set()

    def starting(*, cell, cell_index):
        nonlocal current
        current = cell_index + 1
        started.add(cell_index)

    client = NotebookClient(nb, timeout=timeout, kernel_name=selected,
        force_raise_errors=True, resources={"metadata": {"path": str(file.parent)}},
        on_cell_execute=starting)
    error = None
    try:
        client.execute(env=dict(os.environ, NOTEBOOKPROOF_VERIFY_ACTIVE="1"))
    except Exception as exc:
        error = exc
    try:
        source_status = 'unchanged' if file.read_bytes() == source_bytes else 'changed'
    except OSError:
        source_status = 'unreadable'
    comparisons = _compare_outputs(before, nb, started, current if error else None, compare_outputs)
    compared = any(c['status'] in {'matched', 'changed', 'partial'} for c in comparisons)
    complete = bool(comparisons) and all(c['status'] in {'matched', 'changed'} and not c.get('unsupported') for c in comparisons)
    changed = any(c['status'] == 'changed' for c in comparisons)
    report = dict(executed=bool(started), succeeded=error is None, sourceStatus=source_status,
        outputsCompared=compared, comparisonComplete=complete,
        outputsMatch=False if changed else True if complete else None,
        outputComparisons=comparisons, kernelName=selected, failedCell=current if error else None,
        errorName=getattr(error, "ename", type(error).__name__) if error else None,
        error=str(error)[:2000] if error else None,
        limitations=["Payload comparison is exact, not a test of scientific correctness or semantic equivalence.",
            "Timestamps, floating-point formatting, HTML identifiers and image encoding can legitimately change.",
            "Widgets and unrecognised MIME types are not compared; all comparison gaps are listed.",
            "Code runs with normal permissions and may modify files or call external services.",
            "Nested verify() calls are skipped to prevent recursive kernel creation."])
    if error:
        print(f"Fresh-kernel execution failed at cell {current or '?'}: {report['errorName']}")
        if details:
            print(report["error"])
    else:
        print(f"Fresh-kernel execution completed using {selected}.")
    counts = {status: sum(c['status'] == status for c in comparisons) for status in sorted({c['status'] for c in comparisons})}
    print('Outputs: ' + (', '.join(f'{count} {status}' for status, count in counts.items()) or 'no comparable code cells'))
    if details:
        for comparison in comparisons:
            print(f"cell {comparison['cell']}: {comparison['status']} — {comparison['detail']}")
    print(f"Source file after execution: {source_status}. Inspect comparison gaps; matching payloads do not prove correctness.")
    return report


def _output_payloads(outputs):
    supported = {'text/plain', 'text/html', 'text/markdown', 'text/latex',
                 'application/json', 'image/png', 'image/jpeg', 'image/svg+xml'}
    payloads, unsupported, formats, streams = [], set(), set(), {}

    def text(value):
        return (''.join(value) if isinstance(value, list) else value).replace('\r\n', '\n')

    def flush():
        if streams:
            payloads.append(('stream', dict(streams)))
            streams.clear()

    for output in outputs:
        kind = output.get('output_type')
        if kind == 'stream':
            name = output.get('name', 'stdout')
            formats.add(name)
            streams[name] = streams.get(name, '') + text(output.get('text', ''))
            continue
        flush()
        if kind in {'display_data', 'execute_result'}:
            data = output.get('data', {})
            unsupported.update(set(data) - supported)
            values = {}
            for mime in sorted(set(data) & supported):
                value = data[mime]
                if mime == 'application/json':
                    value = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
                else:
                    value = text(value)
                    if mime in {'image/png', 'image/jpeg'}:
                        value = ''.join(value.split())
                values[mime] = value
                formats.add(mime)
            if values:
                payloads.append(('display', values))
        elif kind == 'error':
            formats.add('error')
            payloads.append(('error', output.get('ename'), output.get('evalue')))
        else:
            unsupported.add(str(kind))
    flush()
    return payloads, sorted(unsupported), sorted(formats)


def _compare_outputs(before, after, started, failed_cell, enabled):
    comparisons = []
    for index, (old, new) in enumerate(zip(before.cells, after.cells)):
        if old.cell_type != 'code' or not old.source.strip():
            continue
        result = dict(cell=index + 1)
        tags = old.metadata.get('tags', [])
        if index not in started:
            result.update(status='skipped' if 'skip-execution' in tags else 'not_run', detail='This code cell was not executed.')
        elif index + 1 == failed_cell:
            result.update(status='failed', detail='Execution failed here; its output was not compared.')
        elif not enabled or 'notebookproof-ignore-output' in tags or old.source.lstrip().startswith('# notebookproof-ignore-output'):
            result.update(status='ignored', detail='Output comparison was explicitly disabled; the code still executed.')
        elif old.execution_count is None and not old.outputs:
            result.update(status='no_baseline', detail='No saved execution count or output is available as a comparison baseline.')
        else:
            old_payload, old_unknown, old_formats = _output_payloads(old.outputs)
            new_payload, new_unknown, new_formats = _output_payloads(new.outputs)
            unknown = sorted(set(old_unknown + new_unknown))
            status = 'changed' if old_payload != new_payload else 'partial' if unknown and (old_payload or new_payload) else 'unsupported' if unknown else 'matched'
            result.update(status=status, unsupported=unknown, mimeTypes=sorted(set(old_formats + new_formats)),
                detail='Saved and fresh supported payloads ' + ('differ; review whether the change is expected.' if status == 'changed' else 'match.')
                    + (' Not compared: ' + ', '.join(unknown) + '.' if unknown else ''))
        comparisons.append(result)
    return comparisons


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
                findings.append(_finding("info", "UNDEFINED", f"Check the deferred reference to {name}",
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
    class Calls(ast.NodeVisitor):
        def __init__(self):
            self.aliases, self.seeded, self.findings = {}, set(), []
            self.cell, self.deferred = None, False

        def visit_Import(self, node):
            self.aliases.update((a.asname or a.name.split('.')[0], a.name if a.asname else a.name.split('.')[0]) for a in node.names)

        def visit_ImportFrom(self, node):
            self.aliases.update((a.asname or a.name, f'{node.module}.{a.name}') for a in node.names)

        def visit_Call(self, node):
            self.generic_visit(node)  # Arguments execute before the outer call can seed anything.
            root, *rest = ast.unparse(node.func).split('.')
            if root not in self.aliases:
                return
            name = '.'.join([self.aliases[root]] + rest)
            supplied = lambda value: not (isinstance(value, ast.Constant) and value.value is None)
            seed_keywords = {'seed', 'random_state'} | ({'a'} if name == 'random.seed' else {'x'} if name == 'random.Random' else set())
            explicit = any(k.arg in seed_keywords and supplied(k.value) for k in node.keywords)
            arg_seed = bool(node.args and supplied(node.args[0]))
            seeds = {'random.seed': 'random', 'numpy.random.seed': 'numpy.random',
                     'torch.manual_seed': 'torch', 'tensorflow.random.set_seed': 'tensorflow.random'}
            if name in seeds:
                if arg_seed or explicit:
                    self.seeded.add(seeds[name])
                else:
                    self.seeded.discard(seeds[name])
                return
            constructors = {'random.Random', 'numpy.random.default_rng', 'numpy.random.RandomState'}
            if name in constructors:
                unseeded = not (explicit or arg_seed)
            else:
                draw = name.startswith(('numpy.random.', 'random.', 'torch.rand', 'tensorflow.random.'))
                draw &= name.rsplit('.', 1)[-1] not in {'getstate', 'setstate', 'get_state', 'set_state', 'Generator', 'BitGenerator'}
                unseeded = draw and not explicit and not any(name.startswith(s + '.') for s in self.seeded)
            if unseeded:
                self.findings.append(_finding('info' if self.deferred else 'warn', 'UNSEEDED_RANDOM',
                    'Review a random call without a visible earlier seed',
                    f'{name} has no seed recognised here. Independent generators need their own seed; deferred calls, control flow and aliases need manual review.', cell=self.cell))

        def visit_FunctionDef(self, node):
            for value in node.decorator_list + node.args.defaults + [v for v in node.args.kw_defaults if v is not None]:
                self.visit(value)
            aliases, seeded, deferred = self.aliases.copy(), self.seeded.copy(), self.deferred
            self.deferred = True
            for statement in node.body:
                self.visit(statement)
            self.aliases, self.seeded, self.deferred = aliases, seeded, deferred

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Lambda(self, node):
            for value in node.args.defaults + [v for v in node.args.kw_defaults if v is not None]:
                self.visit(value)
            aliases, seeded, deferred = self.aliases.copy(), self.seeded.copy(), self.deferred
            self.deferred = True
            self.visit(node.body)
            self.aliases, self.seeded, self.deferred = aliases, seeded, deferred

        def visit_If(self, node):
            self.visit(node.test)
            if isinstance(node.test, ast.Constant):
                for statement in node.body if node.test.value else node.orelse:
                    self.visit(statement)
                return
            aliases, seeded = self.aliases.copy(), self.seeded.copy()
            for statements in (node.body, node.orelse):
                self.aliases, self.seeded = aliases.copy(), seeded.copy()
                for statement in statements:
                    self.visit(statement)
            self.aliases, self.seeded = aliases, seeded

        def visit_For(self, node):
            aliases, seeded = self.aliases.copy(), self.seeded.copy()
            self.generic_visit(node)
            self.aliases, self.seeded = aliases, seeded

        visit_AsyncFor = visit_For
        visit_While = visit_For
        visit_Try = visit_For

    visitor = Calls()
    for cell, tree in zip(cells, trees):
        if tree is not None:
            visitor.cell = cell['index']
            visitor.visit(tree)
    return visitor.findings


def frame(df, *, keys=None, max_rows=None, rules=None, details=False):
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
    declared = _validate_rules(rules, headers)
    report = _analyze_columns(headers, values, masks, len(sample), keys=key_names, dtypes=dtypes)
    for finding in report['findings']:
        if finding['code'] in {'MISSING', 'EMPTY_COL', 'DUP_ROWS'}:
            finding['severity'] = 'info'
        elif finding['code'] in {'DUP_KEY', 'MISSING_KEY'}:
            finding['severity'] = 'error'
    report['findings'].extend(_rule_findings(headers, values, masks, declared))
    violations = any(f['severity'] == 'error' and (f['code'].startswith('RULE_') or f['code'] in {'DUP_KEY', 'MISSING_KEY'}) for f in report['findings'])
    skipped = any(f['code'] == 'RULE_SKIPPED' for f in report['findings']) or any(
        report['columns'][headers.index(name)]['unique'] is None for name in key_names)
    report.update(totalRows=total, truncated=len(sample) < total, rules=declared, keys=key_names,
        rulesPassed=False if violations else None if skipped or not len(sample) or not (declared or key_names) else True)
    report['findings'].sort(key=lambda f: {'error': 0, 'warn': 1, 'info': 2}[f['severity']])
    _print_table(report, details=details)
    return report


def _validate_rules(rules, headers):
    if rules is None:
        return {}
    if not isinstance(rules, dict):
        raise ValueError('rules must map column names to nullable, unique, min or max rules.')
    for name, constraints in rules.items():
        if name not in headers or not isinstance(constraints, dict):
            raise ValueError('Every rule must refer to an existing column and contain a rule dictionary.')
        if set(constraints) - {'nullable', 'unique', 'min', 'max'}:
            raise ValueError('Supported rules are nullable, unique, min and max.')
        for kind, limit in constraints.items():
            if kind in {'nullable', 'unique'}:
                if type(limit) is not bool:
                    raise ValueError(f'{kind} must be True or False.')
            elif isinstance(limit, bool) or not isinstance(limit, (Real, Decimal)) or not math.isfinite(limit):
                raise ValueError(f'{kind} must be a finite number.')
        if 'min' in constraints and 'max' in constraints and constraints['min'] > constraints['max']:
            raise ValueError('A min rule cannot exceed its max rule.')
    return copy.deepcopy(rules)


def _rule_findings(headers, values, masks, rules):
    findings = []
    for name, constraints in rules.items():
        index = headers.index(name)
        present = [(row, value) for row, (value, missing) in enumerate(zip(values[index], masks[index]), 1) if not missing]
        counts = {}
        if constraints.get('nullable') is False:
            counts['NULL'] = ([row for row, missing in enumerate(masks[index], 1) if missing], 'missing values violate nullable=False')
        if constraints.get('unique'):
            try:
                seen, repeated = set(), []
                for row, value in present:
                    key = _value_key(value)
                    if key in seen:
                        repeated.append(row)
                    seen.add(key)
                counts['UNIQUE'] = (repeated, 'repeated non-null values violate unique=True')
            except (TypeError, ValueError):
                findings.append(_finding('warn', 'RULE_SKIPPED', f'Uniqueness of {name} could not be checked',
                    'Unsupported unhashable objects prevent evaluation of the declared unique rule.', column=name))
        if 'min' in constraints or 'max' in constraints:
            numeric = [(row, value) for row, value in present if isinstance(value, (Real, Decimal)) and not isinstance(value, bool)]
            numeric_rows = {row for row, _ in numeric}
            counts['TYPE'] = ([row for row, _ in present if row not in numeric_rows], 'non-numeric values cannot satisfy a numeric range rule')
            if 'min' in constraints:
                counts['MIN'] = ([row for row, value in numeric if value < constraints['min']], f"values are below the declared minimum {constraints['min']}")
            if 'max' in constraints:
                counts['MAX'] = ([row for row, value in numeric if value > constraints['max']], f"values are above the declared maximum {constraints['max']}")
        for kind, (positions, description) in counts.items():
            if positions:
                findings.append(_finding('error', 'RULE_' + kind, f'{len(positions)} {description}',
                    'First affected row positions (1-based): ' + ', '.join(map(str, positions[:10]))
                    + '. Review these rows or revise the rule if it does not describe the intended data.',
                    column=name, count=len(positions), rowPositions=positions[:10]))
    return findings


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
        null_keys = sum(any(masks[i][row] for i in indexes) for row in range(nrows))
        if null_keys:
            findings.append(_finding("warn", "MISSING_KEY", f"{null_keys} rows have an incomplete declared key",
                "Declared key: " + ", ".join(map(str, keys)) + ". Review nulls in these columns."))
        if all(keyed[i] is not None for i in indexes):
            key_rows = list(zip(*(keyed[i] for i in indexes)))
            extra = len(key_rows) - len(set(key_rows))
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


def _print_findings(report, *, details=False):
    if not report["findings"]:
        print("No findings from the checks performed.")
    for f in report["findings"]:
        if f['severity'] == 'info' and not details:
            continue
        where = f"  cell {f['cell'] + 1}" if "cell" in f else f"  {f['column']}" if "column" in f else ""
        print(f"{f['severity'].upper():5}  {f['code']}{where}: {f['title']}")
        print(f"       {f['detail']}")
    info = sum(f['severity'] == 'info' for f in report['findings'])
    if info and not details:
        print(f'{info} informational observations retained in the report; use details=True to show them.')


def _print_notebook(report, *, details=False):
    print(f"notebookproof: {report['codeCells']} code cells; {len(report['findings'])} findings")
    print(report.get("where", ""))
    print(report["headline"])
    _print_findings(report, details=details)
    if details:
        for cell in report["cells"]:
            print(f"cell {cell['index'] + 1}: writes {', '.join(cell['defines']) or '-'}; reads {', '.join(cell['uses'] + cell['deferred']) or '-'}")


def _print_table(report, *, details=False):
    print(f"notebookproof: {report['rows']} checked rows x {report['cols']} columns; {len(report['findings'])} findings")
    if report["truncated"]:
        print(f"Sample only: first {report['rows']} of {report['totalRows']} rows. Later rows were not checked.")
    print(report["headline"])
    if report.get('rulesPassed') is not None:
        print('Declared rules: ' + ('passed on checked rows' if report['rulesPassed'] else 'violations found'))
    _print_findings(report, details=details)


def save_report(report, path='notebookproof-report.html', *, overwrite=False):
    """Write a standalone local HTML summary, excluding code and raw output/data values."""
    if not isinstance(report, dict) or not isinstance(report.get('findings'), list):
        raise ValueError('Pass a report returned by check() or frame().')
    file = Path(path).expanduser()
    if file.suffix.lower() != '.html':
        raise ValueError('Choose an .html file; original notebook and data files cannot be overwritten.')
    if type(overwrite) is not bool:
        raise ValueError('overwrite must be True or False.')
    if os.environ.get('NOTEBOOKPROOF_VERIFY_ACTIVE') == '1':
        print('HTML export skipped inside fresh verification; the existing report was not written.')
        return file.resolve()
    esc = lambda value: html.escape(str(value), quote=True)
    findings = report['findings']
    summary = ', '.join(f"{sum(f['severity'] == kind for f in findings)} {kind}" for kind in ('error', 'warn', 'info'))
    context = (f"{report['rows']} of {report.get('totalRows', report['rows'])} rows; {report['cols']} columns"
               if 'rows' in report else f"{report.get('codeCells', 0)} code cells")
    rows = []
    for finding in findings:
        location = f"Cell {finding['cell'] + 1}" if 'cell' in finding else str(finding.get('column', 'Notebook / table'))
        rows.append('<tr>' + ''.join('<td>' + esc(value) + '</td>' for value in
            (finding['severity'].upper(), location, finding['title'], finding['detail'])) + '</tr>')
    execution = report.get('execution')
    run_section = ''
    if execution:
        status = 'Skipped' if execution.get('skipped') else 'Completed' if execution.get('succeeded') else 'Failed'
        comparison_rows = ''.join('<tr>' + ''.join('<td>' + esc(value) + '</td>' for value in
            (c['cell'], c['status'], c['detail'])) + '</tr>' for c in execution['outputComparisons'])
        run_section = f'<h2>Fresh run: {esc(status)}</h2><p>Kernel: {esc(execution.get("kernelName", "not started"))}. Source file after execution: {esc(execution.get("sourceStatus", "not checked"))}. Payload comparison complete: {esc(execution["comparisonComplete"])}.</p><table><thead><tr><th>Cell</th><th>Comparison</th><th>Evidence</th></tr></thead><tbody>{comparison_rows}</tbody></table>'
    rules_section = ''
    if report.get('rules') or report.get('keys'):
        state = 'passed on checked rows' if report['rulesPassed'] is True else 'violations confirmed' if report['rulesPassed'] is False else 'not fully evaluated'
        rule_items = [str(name) + ': ' + ', '.join(f'{kind}={value}' for kind, value in constraints.items()) for name, constraints in report.get('rules', {}).items()]
        if report.get('keys'):
            rule_items.insert(0, 'Unique key: ' + ', '.join(map(str, report['keys'])))
        rules_section = f'<h2>Declared expectations: {esc(state)}</h2><ul>' + ''.join('<li>' + esc(rule) + '</li>' for rule in rule_items) + '</ul>'
    dependencies = ''
    if report.get('cells'):
        dependency_rows = ''.join('<tr>' + ''.join('<td>' + esc(value) + '</td>' for value in
            (c['index'] + 1, ', '.join(c['defines']), ', '.join(c['uses']), ', '.join(c['deferred']))) + '</tr>' for c in report['cells'])
        dependencies = f'<details><summary>Cell dependencies</summary><table><thead><tr><th>Cell</th><th>Writes</th><th>Immediate reads</th><th>Deferred reads</th></tr></thead><tbody>{dependency_rows}</tbody></table></details>'
    limits = list(report.get('limitations', [])) + (execution.get('limitations', []) if execution else [])
    if report.get('truncated'):
        limits.insert(0, 'Only the first requested rows were checked. Later rows were not inspected.')
    limits.append('This summary omits source code, raw outputs and example data values. Column and variable names and finding descriptions remain; review before sharing.')
    document = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Notebookproof report</title><style>
body{{font:16px/1.6 system-ui,sans-serif;color:#202b37;background:#f6f7f9;margin:0}}main{{max-width:1100px;margin:36px auto;background:white;padding:32px;border:1px solid #d9dfe5;border-radius:12px}}h1,h2{{line-height:1.2}}table{{width:100%;border-collapse:collapse;margin:20px 0}}th,td{{text-align:left;padding:12px;border-bottom:1px solid #d9dfe5;vertical-align:top;overflow-wrap:anywhere}}th{{background:#eef2f6}}summary{{cursor:pointer;font-weight:600}}footer{{font-size:14px;color:#586575}}@media(max-width:700px){{main{{margin:8px;padding:16px}}table{{display:block;overflow-x:auto}}}}</style></head><body><main>
<h1>Notebookproof report</h1><p>{esc(report.get('headline', 'Inspection evidence'))}</p><p>{esc(context)} · {esc(summary)}</p>
{run_section}{rules_section}<h2>Findings</h2><table><thead><tr><th>Severity</th><th>Location</th><th>Finding</th><th>Next step / evidence</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="4">No findings from the checks performed.</td></tr>'}</tbody></table>{dependencies}
<h2>Inspection limits</h2><ul>{''.join('<li>' + esc(limit) + '</li>' for limit in limits)}</ul><footer>Generated locally by notebookproof {esc(__version__)}. Matching outputs and satisfied rules do not establish analytical correctness.</footer></main></body></html>'''
    with file.open('w' if overwrite else 'x', encoding='utf-8') as stream:
        stream.write(document)
    return file.resolve()


def _load_notebook(path):
    if path is not None:
        file = Path(path).expanduser()
        if not file.is_file():
            raise FileNotFoundError(f"No notebook at {file}. Save it, then pass the file name.")
        return json.loads(file.read_text(encoding="utf-8-sig")), f"Read {file.resolve()}. Save first if you just edited it.", file.resolve()
    live = _colab_notebook()
    if live is not None:
        return live, "Read the live Colab document; execution and live kernel state were not verified.", None
    found = _jupyter_notebook_path()
    if found is None:
        raise RuntimeError("The notebook path could not be discovered. Save it, then call notebookproof.check('your-notebook.ipynb'). frame(df) does not need a file.")
    file = Path(found)
    return json.loads(file.read_text(encoding="utf-8-sig")), f"Read {file.resolve()}. Save first if you just edited it.", file.resolve()


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
