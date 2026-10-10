"""Check a notebook and a dataframe from inside Jupyter.

    %pip install git+https://github.com/scottcampbelldata/notebookproof.git
    import notebookproof
    notebookproof.check()
    notebookproof.frame(df)

Save the notebook before check(). frame() looks at a table already in memory.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

KEYWORDS = {
    "and", "as", "assert", "async", "await", "break", "class", "continue", "def",
    "del", "elif", "else", "except", "False", "finally", "for", "from", "global",
    "if", "import", "in", "is", "lambda", "None", "nonlocal", "not", "or", "pass",
    "raise", "return", "True", "try", "while", "with", "yield", "match", "case",
}
AMBIENT = {
    "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes", "callable",
    "chr", "classmethod", "compile", "complex", "delattr", "dict", "dir", "divmod",
    "enumerate", "eval", "exec", "filter", "float", "format", "frozenset", "getattr",
    "globals", "hasattr", "hash", "help", "hex", "id", "input", "int", "isinstance",
    "issubclass", "iter", "len", "list", "locals", "map", "max", "memoryview", "min",
    "next", "object", "oct", "open", "ord", "pow", "print", "property", "range",
    "repr", "reversed", "round", "set", "setattr", "slice", "sorted", "staticmethod",
    "str", "sum", "super", "tuple", "type", "vars", "zip", "Exception", "BaseException",
    "ValueError", "TypeError", "KeyError", "IndexError", "AttributeError",
    "StopIteration", "RuntimeError", "NotImplementedError", "Warning", "FileNotFoundError",
    "ZeroDivisionError", "NameError", "OSError", "ImportError", "display", "get_ipython",
    "clear_output", "Markdown", "HTML", "Image", "Math", "self", "cls",
}
MISSING = {"", "na", "n/a", "null", "none", "nan", "nat", "-", "?", ".", "nil"}


def check(path=None):
    """Check the notebook you are in. Pass a path if Jupyter hides the file."""
    nb, where = _load_notebook(path)
    report = analyze_notebook(nb)
    report["where"] = where
    _print_notebook(report)
    return report


def notebook(path):
    """Check a .ipynb on disk."""
    return check(path)


def frame(df):
    """Check a pandas or polars table that is already in memory."""
    text = _table_to_csv(df)
    headers, rows, truncated = parse_table(text)
    report = analyze_table(headers, rows, truncated)
    _print_table(report)
    return report


def analyze_notebook(nb):
    cells_in = nb.get("cells") if isinstance(nb, dict) else None
    if not isinstance(cells_in, list):
        raise ValueError("That is not a Jupyter notebook. Expected a .ipynb with a cells list.")

    drafts = []
    code_index = 0
    for index, cell in enumerate(cells_in):
        kind = "code" if cell.get("cell_type") == "code" else "markdown"
        source = _cell_source(cell)
        if kind != "code":
            drafts.append({"index": index, "codeIndex": -1, "kind": kind, "source": source, "executionCount": None, "defines": [], "uses": []})
            continue
        symbols = cell_symbols(source)
        count = cell.get("execution_count")
        drafts.append({
            "index": index,
            "codeIndex": code_index,
            "kind": "code",
            "source": source,
            "executionCount": count if isinstance(count, int) else None,
            "defines": symbols["defines"],
            "uses": symbols["uses"],
        })
        code_index += 1

    code = [d for d in drafts if d["kind"] == "code"]
    defined_at = {}
    for cell in code:
        for name in cell["defines"]:
            defined_at.setdefault(name, []).append(cell["codeIndex"])

    findings = []
    cells = []
    for d in drafts:
        if d["kind"] != "code":
            cells.append({
                "index": d["index"], "codeIndex": -1, "kind": "markdown",
                "preview": _preview(re.sub(r"^#+\s*", "", d["source"])),
                "executionCount": None, "defines": [], "reads": [], "stale": False,
            })
            continue
        reads = []
        for name in d["uses"]:
            owners = defined_at.get(name, [])
            prior = next((i for i in reversed(owners) if i < d["codeIndex"]), None)
            later = next((i for i in owners if i > d["codeIndex"]), None)
            reads.append({"name": name, "from": prior if prior is not None else later})
        cells.append({
            "index": d["index"], "codeIndex": d["codeIndex"], "kind": "code",
            "source": d["source"], "preview": _preview(d["source"]),
            "executionCount": d["executionCount"], "defines": d["defines"],
            "reads": reads, "stale": False,
        })

    code_reports = [c for c in cells if c["kind"] == "code"]
    by_code = {c["codeIndex"]: c for c in code_reports}

    for cell in code_reports:
        for read in cell["reads"]:
            n = cell["codeIndex"] + 1
            if read["from"] is None:
                findings.append({
                    "severity": "error", "code": "UNDEFINED",
                    "title": f"{read['name']} is never defined",
                    "detail": f"Cell {n} reads {read['name']}, but no code cell assigns or imports it.",
                    "cell": cell["codeIndex"],
                })
            elif read["from"] > cell["codeIndex"]:
                findings.append({
                    "severity": "error", "code": "FORWARD_REF",
                    "title": f"{read['name']} is used before it is defined",
                    "detail": f"Cell {n} needs {read['name']}, which is only created in cell {read['from'] + 1}. Restart-and-run-all will fail here.",
                    "cell": cell["codeIndex"],
                })
                cell["stale"] = True
        if re.search(r"^\s*(%|!)", cell.get("source", ""), re.M):
            findings.append({
                "severity": "warn", "code": "MAGIC",
                "title": "Notebook magic will not run as a script",
                "detail": f"Cell {cell['codeIndex'] + 1} uses % or ! magics. Those work in Jupyter and disappear in plain Python.",
                "cell": cell["codeIndex"],
            })
        if re.search(r"from\s+\S+\s+import\s+\*", cell.get("source", "")):
            findings.append({
                "severity": "warn", "code": "STAR_IMPORT",
                "title": "Star import hides where names come from",
                "detail": f"Cell {cell['codeIndex'] + 1} imports with *.",
                "cell": cell["codeIndex"],
            })

    counts = [c["executionCount"] for c in code_reports]
    ran = [n for n in counts if n is not None]
    if any(ran[i] < ran[i - 1] for i in range(1, len(ran))):
        shown = " → ".join("—" if n is None else str(n) for n in counts)
        findings.append({
            "severity": "error", "code": "OUT_OF_ORDER",
            "title": "Cells were executed out of visual order",
            "detail": f"Execution counts go {shown}. The outputs on screen may not match a top-to-bottom run.",
        })
        for i in range(1, len(code_reports)):
            prev, cur = code_reports[i - 1]["executionCount"], code_reports[i]["executionCount"]
            if prev is not None and cur is not None and cur < prev:
                code_reports[i]["stale"] = True

    unrun = [c for c in code_reports if c["executionCount"] is None and c.get("source", "").strip()]
    if unrun and ran:
        labels = ", ".join(str(c["codeIndex"] + 1) for c in unrun)
        findings.append({
            "severity": "warn", "code": "PARTIAL_RUN",
            "title": "One code cell was never run" if len(unrun) == 1 else f"{len(unrun)} code cells were never run",
            "detail": f"Cells {labels} have no execution count.",
        })
        for c in unrun:
            c["stale"] = True
    elif unrun and not ran and code_reports:
        findings.append({
            "severity": "info", "code": "NEVER_RUN",
            "title": "This notebook has not been executed",
            "detail": "No code cell has an execution count.",
        })

    for name, owners in defined_at.items():
        if len(owners) > 1:
            labels = " and ".join(str(n + 1) for n in owners)
            findings.append({
                "severity": "info", "code": "REDEFINED",
                "title": f"{name} is assigned in more than one cell",
                "detail": f"Cells {labels} all define {name}. Later cells silently replace it.",
            })

    joined = "\n".join(c["source"] for c in code)
    uses_random = re.search(r"\b(np\.random|random\.|torch\.rand|torch\.randn|tf\.random)", joined)
    has_seed = re.search(r"\b(seed|random_state|manual_seed)\b", joined)
    if uses_random and not has_seed:
        findings.append({
            "severity": "warn", "code": "UNSEEDED_RANDOM",
            "title": "Randomness with no seed",
            "detail": "The notebook draws random numbers and never sets a seed or random_state.",
        })

    score = _score(findings, {"error": 16, "warn": 7, "info": 2})
    verdict = "clean" if score >= 85 else "risky" if score >= 55 else "broken"
    errors = sum(1 for f in findings if f["severity"] == "error")
    if verdict == "clean":
        headline = "Top to bottom, this notebook should come back the same."
    elif errors:
        headline = f"{errors} problem{'s' if errors != 1 else ''} would change the result on a fresh run."
    else:
        headline = "It runs, but a fresh kernel will not reliably match what is on screen."
    findings.sort(key=lambda f: ({"error": 0, "warn": 1, "info": 2}[f["severity"]], f.get("cell", 99)))
    return {
        "codeCells": len(code),
        "score": score,
        "verdict": verdict,
        "headline": headline,
        "findings": findings,
        "cells": code_reports,
    }


def cell_symbols(source):
    lines = _logical_lines(_strip_magics(source))
    scopes = [{"indent": -1, "names": set()}]
    defines = []
    uses = set()
    for line in lines:
        while len(scopes) > 1 and line["indent"] <= scopes[-1]["indent"]:
            scopes.pop()
        stmt = _classify(line["code"])
        visible = set()
        for scope in scopes:
            visible |= scope["names"]
        for name in stmt["uses"]:
            if name in AMBIENT or name in visible:
                continue
            uses.add(name)
        is_top = line["indent"] == 0
        bucket = scopes[0] if is_top else scopes[-1]
        for d in stmt["defs"]:
            bucket["names"].add(d)
            if is_top and d not in defines:
                defines.append(d)
        if stmt["opens"]:
            nxt = set(stmt["scopeNames"])
            if not is_top and stmt["defs"]:
                nxt.add(stmt["defs"][0])
            scopes.append({"indent": line["indent"], "names": nxt})
    return {"defines": defines, "uses": list(uses)}


def parse_table(text):
    cleaned = text.lstrip("\ufeff").strip()
    if not cleaned:
        return [], [], False
    delim = _detect_delim(cleaned)
    records = _parse_records(cleaned, delim)
    if not records:
        return [], [], False
    headers = _unique_headers(records[0])
    width = len(headers)
    rows = []
    for rec in records[1:]:
        if not any(c.strip() for c in rec):
            continue
        row = rec[:width]
        while len(row) < width:
            row.append("")
        rows.append(row)
    truncated = len(rows) > 8000
    return headers, rows[:8000], truncated


def analyze_table(headers, rows, truncated=False):
    findings = []
    if not headers:
        return {
            "rows": 0, "cols": 0, "score": 0, "verdict": "poor",
            "headline": "No table found.",
            "findings": [{"severity": "error", "code": "EMPTY", "title": "Nothing to check", "detail": "Pass a dataframe with columns."}],
            "columns": [],
        }
    columns = []
    for col, name in enumerate(headers):
        values = [(r[col] if col < len(r) else "") for r in rows]
        present = [v for v in values if not _missing(v)]
        unique = {v.strip() for v in present}
        kind = "empty"
        numeric = None
        if present:
            votes = {"integer": 0, "number": 0, "boolean": 0, "date": 0, "text": 0}
            for v in present:
                if _looks_int(v):
                    votes["integer"] += 1
                elif _looks_num(v):
                    votes["number"] += 1
                elif _looks_bool(v):
                    votes["boolean"] += 1
                elif _looks_date(v):
                    votes["date"] += 1
                else:
                    votes["text"] += 1
            top, top_n = max(votes.items(), key=lambda kv: kv[1])
            kind = "mixed" if top_n / len(present) < 0.97 else top
        if kind in ("integer", "number"):
            nums = [float(v) for v in present if _looks_num(v)]
            if nums:
                ordered = sorted(nums)
                mean = sum(nums) / len(nums)
                q1 = ordered[int((len(ordered) - 1) * 0.25)]
                q3 = ordered[int((len(ordered) - 1) * 0.75)]
                iqr = q3 - q1
                outlier = 0 if iqr == 0 else sum(1 for n in nums if n < q1 - 1.5 * iqr or n > q3 + 1.5 * iqr)
                mid = len(ordered) // 2
                med = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
                numeric = {"min": ordered[0], "max": ordered[-1], "mean": mean, "median": med, "outlier": outlier}
        columns.append({
            "name": name, "kind": kind, "missing": len(values) - len(present),
            "missingRate": (len(values) - len(present)) / len(values) if values else 0,
            "unique": len(unique), "constant": len(present) > 0 and len(unique) <= 1,
            "examples": list(unique)[:3], "numeric": numeric,
        })

    seen = {}
    duplicate_rows = 0
    for row in rows:
        key = "\x1f".join(row)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            duplicate_rows += 1

    for col in columns:
        if col["kind"] == "empty":
            findings.append({"severity": "warn", "code": "EMPTY_COL", "title": f"{col['name']} is empty", "detail": "Every value is blank or a null token.", "column": col["name"]})
        elif col["missingRate"] >= 0.5:
            findings.append({"severity": "error", "code": "MOSTLY_MISSING", "title": f"{col['name']} is more than half empty", "detail": f"{round(col['missingRate'] * 100)}% of rows have no {col['name']}.", "column": col["name"]})
        elif col["missingRate"] >= 0.15:
            findings.append({"severity": "warn", "code": "MISSING", "title": f"{col['name']} is missing often", "detail": f"{round(col['missingRate'] * 100)}% missing ({col['missing']} rows).", "column": col["name"]})
        if col["constant"]:
            findings.append({"severity": "warn", "code": "CONSTANT", "title": f"{col['name']} never changes", "detail": f"Every present value is “{col['examples'][0] if col['examples'] else ''}”.", "column": col["name"]})
        if col["kind"] == "mixed":
            examples = ", ".join(f"“{e}”" for e in col["examples"])
            findings.append({"severity": "error", "code": "MIXED", "title": f"{col['name']} mixes types", "detail": f"Most values do not share one type. Examples: {examples}.", "column": col["name"]})
        present_n = max(1, len(rows) - col["missing"])
        if col["numeric"] and col["numeric"]["outlier"] > 0 and col["numeric"]["outlier"] / present_n > 0.02:
            findings.append({"severity": "info", "code": "OUTLIERS", "title": f"{col['name']} has outlier values", "detail": f"{col['numeric']['outlier']} values sit outside the usual spread.", "column": col["name"]})
        if re.search(r"(?:^|_)id$", col["name"], re.I) and col["unique"] < len(rows) - col["missing"]:
            extra = (len(rows) - col["missing"]) - col["unique"]
            findings.append({"severity": "warn", "code": "DUP_KEY", "title": f"{col['name']} is not unique", "detail": f"{extra} extra rows share an id.", "column": col["name"]})

    if duplicate_rows:
        findings.append({"severity": "warn", "code": "DUP_ROWS", "title": f"{duplicate_rows} duplicate row{'s' if duplicate_rows != 1 else ''}", "detail": "Identical rows inflate counts. Deduplicate before you trust a rate."})

    numeric_idx = [i for i, c in enumerate(columns) if c["kind"] in ("integer", "number")]
    correlations = []
    for a in range(len(numeric_idx)):
        for b in range(a + 1, len(numeric_idx)):
            ia, ib = numeric_idx[a], numeric_idx[b]
            xs, ys = [], []
            for row in rows:
                x = row[ia] if ia < len(row) else ""
                y = row[ib] if ib < len(row) else ""
                if _missing(x) or _missing(y) or not _looks_num(x) or not _looks_num(y):
                    continue
                xs.append(float(x))
                ys.append(float(y))
            r = _pearson(xs, ys)
            if r is not None and abs(r) >= 0.9:
                correlations.append((columns[ia]["name"], columns[ib]["name"], r))
    correlations.sort(key=lambda t: abs(t[2]), reverse=True)
    for a, b, r in correlations[:6]:
        findings.append({
            "severity": "warn" if abs(r) >= 0.98 else "info",
            "code": "CORRELATED",
            "title": f"{a} and {b} move together",
            "detail": f"Correlation is {r:.2f}. One may be a copy of the other.",
        })

    score = _score(findings, {"error": 14, "warn": 7, "info": 2})
    verdict = "healthy" if score >= 85 else "watch" if score >= 55 else "poor"
    errors = sum(1 for f in findings if f["severity"] == "error")
    if verdict == "healthy":
        headline = "Nothing here should surprise you downstream."
    elif errors:
        headline = f"{errors} issue{'s' if errors != 1 else ''} will change a result if you ignore {'them' if errors != 1 else 'it'}."
    else:
        headline = "Usable, but clean it before you share a number."
    findings.sort(key=lambda f: {"error": 0, "warn": 1, "info": 2}[f["severity"]])
    return {
        "rows": len(rows), "cols": len(headers), "duplicateRows": duplicate_rows,
        "score": score, "verdict": verdict, "headline": headline,
        "findings": findings, "columns": columns, "truncated": truncated,
    }


def _print_notebook(report):
    print(f"notebookproof  {report['score']}/100  {report['verdict']}")
    if report.get("where"):
        print(report["where"])
    print(report["headline"])
    print()
    if not report["findings"]:
        print("No findings.")
    for f in report["findings"]:
        where = f"  cell {f['cell'] + 1}" if "cell" in f else ""
        print(f"{f['severity'].upper():5}  {f['code']}{where}")
        print(f"      {f['title']}")
        print(f"      {f['detail']}")
    print()
    for cell in report["cells"]:
        ran = "never run" if cell["executionCount"] is None else f"ran {cell['executionCount']}"
        flag = "  stale" if cell["stale"] else ""
        defs = ", ".join(cell["defines"]) or "—"
        print(f"cell {cell['codeIndex'] + 1}  {ran}{flag}  writes {defs}")
        print(f"    {cell['preview']}")


def _print_table(report):
    print(f"notebookproof  {report['score']}/100  {report['verdict']}  {report['rows']} rows × {report['cols']} cols")
    print(report["headline"])
    print()
    if not report["findings"]:
        print("No findings.")
    for f in report["findings"]:
        col = f"  {f['column']}" if f.get("column") else ""
        print(f"{f['severity'].upper():5}  {f['code']}{col}")
        print(f"      {f['title']}")
        print(f"      {f['detail']}")


def _load_notebook(path):
    if path:
        file = Path(path).expanduser()
        if not file.is_file():
            raise FileNotFoundError(f"No notebook at {file}. Save it, then pass the file name.")
        return json.loads(file.read_text(encoding="utf-8")), f"Read {file.name}. Save first if you just edited it."
    live = _colab_notebook()
    if live is not None:
        return live, "Read the live Colab notebook."
    found = _jupyter_notebook_path()
    if found is None:
        raise RuntimeError(
            "Jupyter did not tell the kernel which file this is. "
            "Save the notebook, then call notebookproof.check('your-notebook.ipynb'). "
            "frame(df) does not need the file."
        )
    return json.loads(Path(found).read_text(encoding="utf-8")), f"Read {Path(found).name}. Save first if you just edited it."


def _colab_notebook():
    try:
        from google.colab import _message  # type: ignore
    except Exception:
        return None
    try:
        reply = _message.blocking_request("get_ipynb", timeout_sec=8)
    except Exception:
        return None
    nb = reply.get("ipynb") if isinstance(reply, dict) else None
    return nb if isinstance(nb, dict) and isinstance(nb.get("cells"), list) else None


def _jupyter_notebook_path():
    try:
        import ipynbname  # type: ignore
        return str(ipynbname.path())
    except Exception:
        pass
    kernel_id = _kernel_id()
    if not kernel_id:
        return None
    try:
        from jupyter_server.serverapp import list_running_servers  # type: ignore
    except Exception:
        return None
    import urllib.request
    for server in list_running_servers():
        url = str(server.get("url", "")).rstrip("/")
        token = server.get("token") or ""
        if not url:
            continue
        req = urllib.request.Request(f"{url}/api/sessions")
        if token:
            req.add_header("Authorization", f"token {token}")
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:
                sessions = json.loads(resp.read().decode())
        except Exception:
            continue
        root = server.get("root_dir") or server.get("notebook_dir") or "."
        for sess in sessions:
            if (sess.get("kernel") or {}).get("id") != kernel_id:
                continue
            nb_path = sess.get("path") or (sess.get("notebook") or {}).get("path")
            if nb_path:
                return str(Path(root) / nb_path)
    return None


def _kernel_id():
    try:
        from ipykernel.connect import get_connection_file  # type: ignore
    except Exception:
        return None
    stem = Path(get_connection_file()).stem
    return stem[len("kernel-"):] if stem.startswith("kernel-") else None


def _table_to_csv(df):
    if hasattr(df, "to_csv"):
        import io
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        return buf.getvalue()
    if hasattr(df, "write_csv"):
        text = df.write_csv()
        if isinstance(text, str):
            return text
    raise TypeError("Pass a pandas DataFrame or a polars DataFrame.")


def _score(findings, weight):
    score = 100
    for f in findings:
        score -= weight[f["severity"]]
    return max(0, min(100, score))


def _cell_source(cell):
    source = cell.get("source") or ""
    return "".join(source) if isinstance(source, list) else str(source)


def _preview(source):
    for line in source.split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            return line[:88]
    return "(empty)"


def _mask(src):
    out = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c == "#":
            while i < n and src[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if c == "\\":
            out.append(" ")
            i += 1
            if i < n:
                out.append("\n" if src[i] == "\n" else " ")
                i += 1
            continue
        prefix = bool(re.match(r"[rRuUbBfF]", c)) and i + 1 < n and src[i + 1] in "'\""
        q_start = i if c in "'\"" else i + 1 if prefix else -1
        if q_start >= 0:
            if prefix:
                out.append(" ")
                i += 1
            q = src[i]
            triple = src[i:i + 3] == q * 3
            closer = q * 3 if triple else q
            out.append(" " * len(closer))
            i += len(closer)
            while i < n and src[i:i + len(closer)] != closer:
                if src[i] == "\\" and not triple:
                    out.append("  ")
                    i += 2
                    continue
                out.append("\n" if src[i] == "\n" else " ")
                i += 1
            if i < n:
                out.append(" " * len(closer))
                i += len(closer)
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _logical_lines(raw):
    lines = _mask(raw).split("\n")
    out = []
    buf = None
    depth = 0

    def apply_depth(text):
        nonlocal depth
        for ch in text:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth = max(0, depth - 1)

    for line in lines:
        trimmed = line.strip()
        if buf is None:
            if not trimmed:
                continue
            indent = len(line) - len(line.lstrip(" "))
            buf = {"indent": indent, "code": trimmed}
            depth = 0
            apply_depth(trimmed)
            if depth == 0:
                out.append(buf)
                buf = None
            continue
        buf["code"] += " " + trimmed
        apply_depth(trimmed)
        if depth == 0:
            out.append(buf)
            buf = None
    if buf:
        out.append(buf)
    return out


def _strip_magics(src):
    return "\n".join("" if re.match(r"\s*(%%|%|!)", line) else line for line in src.split("\n"))


def _value_idents(code):
    no_attr = re.sub(r"\.([A-Za-z_]\w*)", " ", code)
    no_kw = re.sub(r"([A-Za-z_]\w*)\s*(?==)", " ", no_attr)
    return [n for n in re.findall(r"[A-Za-z_]\w*", no_kw) if n not in KEYWORDS]


def _depth_zero_equals(code):
    idxs = []
    depth = 0
    for i, c in enumerate(code):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "=" and depth == 0:
            prev = code[i - 1] if i else ""
            nxt = code[i + 1] if i + 1 < len(code) else ""
            if prev in "=!<>" or nxt == "=":
                continue
            idxs.append(i)
    return idxs


def _simple_targets(left):
    if re.search(r"[\[.]", left):
        return None
    cleaned = re.sub(r"[()]", " ", left).strip()
    if not cleaned:
        return []
    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    if all(re.fullmatch(r"[A-Za-z_]\w*", p) for p in parts):
        return parts
    return None


def _classify(code):
    empty = {"defs": [], "uses": [], "scopeNames": [], "opens": False}
    def_m = re.fullmatch(r"(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\((.*)\)\s*(?:->\s*[^:]+)?\s*:?\s*", code)
    if def_m:
        params = []
        for p in def_m.group(2).split(","):
            p = re.sub(r"[=:].*$", "", p.strip().lstrip("*")).strip()
            if re.fullmatch(r"[A-Za-z_]\w*", p):
                params.append(p)
        return {
            "defs": [def_m.group(1)],
            "uses": [n for n in _value_idents(def_m.group(2)) if n not in params and n != def_m.group(1)],
            "scopeNames": params,
            "opens": True,
        }
    class_m = re.match(r"^class\s+([A-Za-z_]\w*)", code)
    if class_m:
        return {
            "defs": [class_m.group(1)],
            "uses": [n for n in _value_idents(code) if n not in (class_m.group(1), "class")],
            "scopeNames": [],
            "opens": True,
        }
    if re.match(r"^from\s+\S+\s+import\s+\*", code):
        return empty
    from_m = re.match(r"^from\s+[A-Za-z_][\w.]*\s+import\s+(.+)$", code)
    if from_m:
        defs = []
        for part in from_m.group(1).split(","):
            bits = re.split(r"\s+as\s+", part.strip())
            bound = (bits[1] if len(bits) > 1 else bits[0]).strip()
            if re.fullmatch(r"[A-Za-z_]\w*", bound):
                defs.append(bound)
        return {"defs": defs, "uses": [], "scopeNames": [], "opens": False}
    if code.startswith("import "):
        defs = []
        for part in code[len("import "):].split(","):
            bits = re.split(r"\s+as\s+", part.strip())
            mod = (bits[1] if len(bits) > 1 else bits[0]).strip().split(".")[0]
            if re.fullmatch(r"[A-Za-z_]\w*", mod):
                defs.append(mod)
        return {"defs": defs, "uses": [], "scopeNames": [], "opens": False}
    for_m = re.fullmatch(r"(?:async\s+)?for\s+(.+?)\s+in\s+([\s\S]+?)\s*:?\s*", code)
    if for_m:
        targets = _simple_targets(for_m.group(1)) or []
        return {"defs": targets, "uses": [n for n in _value_idents(for_m.group(2)) if n not in targets], "scopeNames": [], "opens": False}
    with_m = re.fullmatch(r"with\s+([\s\S]+?)\s*:\s*", code)
    if with_m:
        defs, uses = [], []
        for clause in with_m.group(1).split(","):
            bits = re.split(r"\s+as\s+", clause)
            if len(bits) > 1 and re.fullmatch(r"[A-Za-z_]\w*", bits[1].strip()):
                defs.append(bits[1].strip())
            uses.extend(_value_idents(bits[0]))
        return {"defs": defs, "uses": uses, "scopeNames": [], "opens": False}
    eq = _depth_zero_equals(code)
    if eq:
        first = eq[0]
        op_start = first - 1 if first and code[first - 1] in "+-*/%@&|^" else first
        left, right = code[:op_start], code[first + 1:]
        targets = _simple_targets(left)
        uses = _value_idents(right)
        if targets is not None:
            if op_start != first:
                uses.extend(targets)
            return {"defs": targets, "uses": uses, "scopeNames": [], "opens": False}
        return {"defs": [], "uses": _value_idents(left) + uses, "scopeNames": [], "opens": False}
    comp = set()
    for m in re.finditer(r"\bfor\s+([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s+in\b", code):
        comp.update(s.strip() for s in m.group(1).split(","))
    stripped = re.sub(r"\bfor\s+([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s+in\b", " for ", code)
    return {"defs": [], "uses": [n for n in _value_idents(stripped) if n not in comp], "scopeNames": [], "opens": False}


def _detect_delim(text):
    sample = "\n".join(text.splitlines()[:8])
    counts = {",": sample.count(","), "\t": sample.count("\t"), ";": sample.count(";")}
    if counts["\t"] > counts[","] and counts["\t"] > counts[";"]:
        return "\t"
    if counts[";"] > counts[","]:
        return ";"
    return ","


def _parse_records(text, delim):
    rows, row, cur, q = [], [], "", False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if q:
            if c == '"':
                if i + 1 < n and text[i + 1] == '"':
                    cur += '"'
                    i += 2
                    continue
                q = False
            else:
                cur += c
            i += 1
            continue
        if c == '"':
            q = True
        elif c == delim:
            row.append(cur)
            cur = ""
        elif c in "\n\r":
            if c == "\r" and i + 1 < n and text[i + 1] == "\n":
                i += 1
            row.append(cur)
            rows.append(row)
            row, cur = [], ""
        else:
            cur += c
        i += 1
    if cur or row:
        row.append(cur)
        rows.append(row)
    return rows


def _unique_headers(raw):
    seen = {}
    headers = []
    for i, h in enumerate(raw):
        base = h.strip() or f"column_{i + 1}"
        n = seen.get(base, 0)
        seen[base] = n + 1
        headers.append(base if n == 0 else f"{base}_{n + 1}")
    return headers


def _missing(value):
    return value.strip().lower() in MISSING


def _looks_int(v):
    return re.fullmatch(r"[-+]?\d+", v.strip()) is not None


def _looks_num(v):
    return re.fullmatch(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", v.strip()) is not None


def _looks_bool(v):
    return re.fullmatch(r"(true|false|yes|no|y|n|0|1)", v.strip(), re.I) is not None


def _looks_date(v):
    return re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ tT]\d{2}:\d{2}(?::\d{2})?)?", v.strip()) is not None


def _pearson(xs, ys):
    n = min(len(xs), len(ys))
    if n < 4:
        return None
    mx, my = sum(xs[:n]) / n, sum(ys[:n]) / n
    num = dx = dy = 0.0
    for i in range(n):
        a, b = xs[i] - mx, ys[i] - my
        num += a * b
        dx += a * a
        dy += b * b
    if dx == 0 or dy == 0:
        return None
    return num / (dx ** 0.5 * dy ** 0.5)
