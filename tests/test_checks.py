import lintel


def test_notebook_catches_order_and_forward_ref():
    report = lintel.analyze_notebook({
        "cells": [
            {"cell_type": "code", "execution_count": 4, "source": "print(df.head())\nmodel.fit(X, y)"},
            {"cell_type": "code", "execution_count": 1, "source": "import pandas as pd\ndf = pd.read_csv('sales.csv')\nX = df.drop(columns=['y'])\ny = df['y']"},
            {"cell_type": "code", "execution_count": 2, "source": "from sklearn.linear_model import LinearRegression\nmodel = LinearRegression()"},
            {"cell_type": "code", "execution_count": None, "source": "%matplotlib inline\nprint('never')"},
        ]
    })
    codes = [f["code"] for f in report["findings"]]
    assert "FORWARD_REF" in codes
    assert "OUT_OF_ORDER" in codes
    assert "PARTIAL_RUN" in codes
    assert "MAGIC" in codes
    assert "UNDEFINED" not in codes


def test_clean_notebook():
    report = lintel.analyze_notebook({
        "cells": [
            {"cell_type": "code", "execution_count": 1, "source": "import pandas as pd\ndf = pd.DataFrame({'a':[1]})"},
            {"cell_type": "code", "execution_count": 2, "source": "print(len(df))"},
        ]
    })
    assert report["verdict"] == "clean"


def test_table_flags():
    text = """id,revenue,revenue_copy,flag,signup
1,10,10,yes,2024-01-01
1,10,10,yes,2024-01-01
2,20,20,yes,later
3,30,30,yes,2024-03-03
4,40,40,yes,2024-04-04
5,50,50,yes,2024-05-05
"""
    headers, rows, truncated = lintel.parse_table(text)
    report = lintel.analyze_table(headers, rows, truncated)
    codes = [f["code"] for f in report["findings"]]
    assert "DUP_KEY" in codes
    assert "DUP_ROWS" in codes
    assert "CORRELATED" in codes
    assert "CONSTANT" in codes
    assert "MIXED" in codes
