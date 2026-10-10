# notebook-lintel

Checks you rerun inside Jupyter. It does not execute your cells.

`pip install lintel` is a different project (a docstring checker). This one is `notebook-lintel`.

## Install

In a notebook cell:

```python
%pip install git+https://github.com/scottcampbelldata/notebook-lintel.git
```

Then:

```python
import lintel
lintel.check()      # this notebook
lintel.frame(df)    # a pandas or polars table already in memory
```

Save the notebook before `check()`. The kernel does not know cell order, so Lintel reads the saved `.ipynb`. In VS Code, if it cannot find the file:

```python
lintel.check("your-notebook.ipynb")
```

`frame(df)` does not need the file.

## What it reports

Notebook: cells run out of order, names used before they exist, names never defined, cells that never ran, magics, star imports, and random draws with no seed.

Table: missing values, mixed types, duplicate rows, duplicate ids, constant columns, and columns that are copies of each other.
