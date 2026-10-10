import pandas as pd

import notebookproof


def test_notebook_catches_order_and_forward_ref():
    report = notebookproof.analyze_notebook({
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
    report = notebookproof.analyze_notebook({
        "cells": [
            {"cell_type": "code", "execution_count": 1, "source": "import pandas as pd\ndf = pd.DataFrame({'a':[1]})"},
            {"cell_type": "code", "execution_count": 2, "source": "print(len(df))"},
        ]
    })
    assert report["findings"] == []
    assert report["executed"] is False


def test_table_flags():
    df = pd.DataFrame({
        'id': [1, 1, 2, 3, 4, 5], 'revenue': [10, 10, 20, 30, 40, 50],
        'revenue_copy': [10, 10, 20, 30, 40, 50], 'flag': ['yes'] * 6,
        'signup': [pd.Timestamp('2024-01-01'), pd.Timestamp('2024-01-01'),
                   'later', pd.Timestamp('2024-03-03'), pd.Timestamp('2024-04-04'), pd.Timestamp('2024-05-05')],
    })
    report = notebookproof.frame(df, keys=['id'])
    codes = [f["code"] for f in report["findings"]]
    assert "DUP_KEY" in codes
    assert "DUP_ROWS" in codes
    assert "COPY_COLUMN" in codes
    assert "CONSTANT" in codes
    assert "MIXED" in codes
