import contextlib
import io

import pandas as pd
import pytest

import notebookproof as npf


def notebook(*sources):
    return {'cells': [
        {'cell_type': 'code', 'source': source, 'execution_count': i + 1, 'outputs': []}
        for i, source in enumerate(sources)
    ]}


def codes(report):
    return {f['code'] for f in report['findings']}


@pytest.mark.parametrize('source', [
    'print(f"{missing}")',
    'values = [missing * x for x in range(3)]',
    'def total(amount=missing):\n    return amount',
])
def test_unresolved_names_are_visible(source):
    assert 'UNDEFINED' in codes(npf.analyze_notebook(notebook(source)))


@pytest.mark.parametrize('sources', [
    ('def total():\n    return amount', 'amount = 10', 'total()'),
    ('values = [x * 2 for x in range(3)]',),
    ('values = {x: x * 2 for x in range(3)}',),
    ('from math import (\n    sqrt,\n    pi,\n)', 'sqrt(pi)'),
    ('def outer():\n    x = 1\n    return lambda: x', 'outer()'),
    ('class C:\n    x = 1\n    y = x + 1',),
    ('if True:\n    x = 1\n    print(x)',),
    ('x = 1\nx += 1\nprint(x)',),
])
def test_valid_scopes_do_not_raise_false_name_errors(sources):
    assert not ({'FORWARD_REF', 'UNDEFINED'} & codes(npf.analyze_notebook(notebook(*sources))))


def test_syntax_errors_are_reported():
    assert 'SYNTAX_ERROR' in codes(npf.analyze_notebook(notebook('x =')))


def test_saved_execution_error_is_reported():
    nb = notebook('raise ValueError("bad")')
    nb['cells'][0]['outputs'] = [{'output_type': 'error', 'ename': 'ValueError', 'evalue': 'bad'}]
    assert 'SAVED_ERROR' in codes(npf.analyze_notebook(nb))


def test_report_does_not_claim_reproducibility():
    report = npf.analyze_notebook(notebook('raise ValueError("bad")'))
    assert 'score' not in report and 'verdict' not in report
    assert report['executed'] is False
    assert 'static' in report['headline'].lower()


def test_physical_cell_number_includes_markdown():
    nb = notebook('print(missing)')
    nb['cells'].insert(0, {'cell_type': 'markdown', 'source': '# Notes'})
    finding = next(f for f in npf.analyze_notebook(nb)['findings'] if f['code'] == 'UNDEFINED')
    assert finding['cell'] == 1


def test_random_seed_comment_is_not_a_seed():
    report = npf.analyze_notebook(notebook('import numpy as np\n# seed later\nx = np.random.rand(3)'))
    assert 'UNSEEDED_RANDOM' in codes(report)


def test_seeded_generator_is_recognized():
    report = npf.analyze_notebook(notebook('import numpy as np\nrng = np.random.default_rng(42)\nx = rng.normal(size=3)'))
    assert 'UNSEEDED_RANDOM' not in codes(report)


def test_augmented_assignment_needs_an_existing_value():
    assert 'UNDEFINED' in codes(npf.analyze_notebook(notebook('x += 1')))


def test_dataframe_is_not_changed():
    df = pd.DataFrame({'value': pd.Series([1, None, 3], dtype='Int64')}, index=[3, 2, 1])
    before = df.copy(deep=True)
    npf.frame(df)
    pd.testing.assert_frame_equal(df, before)


def test_legacy_csv_keeps_strings_and_all_null_fields():
    headers, rows, truncated = npf.parse_table('x,y\n,\nNA,1\n')
    assert headers == ['x', 'y'] and rows == [['', ''], ['NA', '1']]
    assert truncated is False


def test_nested_dataframe_values_are_supported():
    df = pd.DataFrame({'value': [[1, 2], [1, 2], {'a': 3}]})
    assert npf.frame(df)['duplicateRows'] == 1


def test_large_finite_numeric_values_do_not_overflow():
    report = npf.frame(pd.DataFrame({'value': [1e308, 1e308, 1e308]}))
    assert report['columns'][0]['numeric']['mean'] == 1e308
    assert report['columns'][0]['numeric']['median'] == 1e308


def test_magic_like_text_in_a_string_is_not_a_command():
    report = npf.analyze_notebook(notebook('message = """Example:\n%time code\n"""'))
    assert 'MAGIC' not in codes(report)


def test_postponed_annotations_do_not_require_immediate_definitions():
    report = npf.analyze_notebook(notebook(
        'from __future__ import annotations\ndef f(x: Later):\n    return x',
        'class Later:\n    pass',
    ))
    assert 'FORWARD_REF' not in codes(report)


def test_future_annotations_apply_to_later_cells():
    report = npf.analyze_notebook(notebook(
        'from __future__ import annotations', 'def f(x: Later):\n    return x', 'class Later:\n    pass'))
    assert 'FORWARD_REF' not in codes(report)


def test_outliers_are_observations_with_no_automatic_cleanup():
    df = pd.DataFrame({'value': list(range(10)) + [1000]})
    report = npf.frame(df)
    assert 'OUTLIERS' in codes(report)
    assert report['columns'][0]['numeric']['outlier'] == 1
    assert len(df) == 11


def test_unsupported_objects_disclose_skipped_duplicate_checks():
    import numpy as np
    df = pd.DataFrame({'value': [{'data': np.array([1])}, {'data': np.array([1])}]})
    report = npf.frame(df)
    assert 'CHECK_SKIPPED' in codes(report)
    assert report['duplicateRows'] is None


def test_null_rows_and_string_tokens_are_preserved():
    report = npf.frame(pd.DataFrame({'value': ['NA', '-', '?', None, None]}))
    assert report['rows'] == 5
    assert report['columns'][0]['missing'] == 2
    assert report['columns'][0]['unique'] == 3


def test_python_types_survive_dataframe_check():
    report = npf.frame(pd.DataFrame({'value': pd.Series([1, '1'], dtype=object)}))
    assert report['duplicateRows'] == 0
    assert 'MIXED' in codes(report)
    assert 'CONSTANT' not in codes(report)


def test_default_checks_all_rows():
    report = npf.frame(pd.DataFrame({'value': list(range(8000)) + [None] * 2000}))
    assert report['rows'] == 10000
    assert report['columns'][0]['missing'] == 2000
    assert report['truncated'] is False


def test_explicit_sampling_is_disclosed():
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        report = npf.frame(pd.DataFrame({'value': range(10000)}), max_rows=8000)
    assert report['totalRows'] == 10000 and report['rows'] == 8000
    assert report['truncated'] is True
    assert 'first 8000 of 10000' in output.getvalue()


def test_keys_are_explicit_and_support_composite_keys():
    df = pd.DataFrame({'account_id': [1, 1, 2], 'period': ['a', 'b', 'a']})
    assert 'DUP_KEY' not in codes(npf.frame(df))
    assert 'DUP_KEY' in codes(npf.frame(df, keys=['account_id']))
    assert 'DUP_KEY' not in codes(npf.frame(df, keys=['account_id', 'period']))


def test_nullable_numbers_do_not_become_mixed():
    df = pd.DataFrame({'value': pd.Series([1, None, 3], dtype='Int64')})
    report = npf.frame(df)
    assert 'MIXED' not in codes(report)
    assert report['columns'][0]['missing'] == 1


def test_correlation_is_not_reported_as_a_copy():
    df = pd.DataFrame({'x': [1, 2, 3, 4], 'y': [2, 4, 6, 8], 'copy': [1, 2, 3, 4]})
    report = npf.frame(df)
    assert 'CORRELATED' in codes(report) and 'COPY_COLUMN' in codes(report)
    assert all(f['severity'] == 'info' for f in report['findings'] if f['code'] == 'CORRELATED')


@pytest.mark.parametrize('max_rows', [0, -1, True, 1.5])
def test_invalid_sampling_limit_is_rejected(max_rows):
    with pytest.raises(ValueError):
        npf.frame(pd.DataFrame({'x': [1]}), max_rows=max_rows)


def test_missing_key_column_is_rejected():
    with pytest.raises(ValueError):
        npf.frame(pd.DataFrame({'x': [1]}), keys=['absent'])


def test_empty_dataframe_has_a_visible_finding():
    assert 'EMPTY' in codes(npf.frame(pd.DataFrame({'x': pd.Series(dtype='int64')})))


def test_polars_preserves_nulls_and_types():
    pl = pytest.importorskip('polars')
    df = pl.DataFrame({'id': [1, 1, 2], 'value': ['NA', None, '?']})
    report = npf.frame(df, keys=['id'])
    assert report['rows'] == 3 and report['columns'][1]['missing'] == 1
    assert 'DUP_KEY' in codes(report)
