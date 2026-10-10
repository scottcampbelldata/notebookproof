# notebookproof

Checks you rerun inside Jupyter. It does not execute your cells.

The install name and the import name are the same.

## Install

In a notebook cell:

```python
%pip install git+https://github.com/scottcampbelldata/notebookproof.git
import notebookproof
notebookproof.check()
notebookproof.frame(df)
```

`check()` reads this notebook. Save it first. The kernel does not know cell order, so notebookproof reads the saved file. In VS Code, if it cannot find the file:

```python
notebookproof.check("your-notebook.ipynb")
```

`frame(df)` checks a pandas or polars table already in memory. It does not need the file.

## What it reports

Notebook: cells run out of order, names used before they exist, names never defined, cells that never ran, magics, star imports, and random draws with no seed.

Table: missing values, mixed types, duplicate rows, duplicate ids, constant columns, and columns that are copies of each other.
