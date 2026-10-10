import json

import nbformat
import pandas as pd
import polars as pl
import pytest

import notebookproof as npf


def saved(tmp_path, *cells):
    path = tmp_path / 'analysis.ipynb'
    nbformat.write(nbformat.v4.new_notebook(cells=list(cells)), path)
    return path


def code(source, text=None, **metadata):
    cell = nbformat.v4.new_code_cell(source, metadata=metadata)
    if text is not None:
        cell.execution_count = 9
        cell.outputs = [nbformat.v4.new_output('stream', name='stdout', text=text)]
    return cell


@pytest.mark.parametrize('old,status', [('2\n', 'matched'), ('1\n', 'changed')])
def test_fresh_outputs_are_compared_and_original_is_preserved(tmp_path, old, status):
    path = saved(tmp_path, nbformat.v4.new_markdown_cell('# Results'), code('print(2)', old))
    before = path.read_bytes()
    report = npf.verify(path)
    assert report['outputComparisons'][0]['cell'] == 2
    assert report['outputComparisons'][0]['status'] == status
    assert report['outputsCompared'] is True
    assert report['comparisonComplete'] is True
    assert report['outputsMatch'] is (status == 'matched')
    assert path.read_bytes() == before


def test_stream_chunks_and_execution_counts_are_not_result_changes(tmp_path):
    cell = code('print("hello world")', 'hello ')
    cell.outputs.append(nbformat.v4.new_output('stream', name='stdout', text=['world', '\n']))
    report = npf.verify(saved(tmp_path, cell))
    assert report['outputComparisons'][0]['status'] == 'matched'


def test_rich_json_and_image_payloads_are_compared(tmp_path):
    data = {'application/json': {'value': 42}, 'text/plain': 'result',
            'image/png': 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aSAAAAABJRU5ErkJggg=='}
    cell = code('from IPython.display import display\ndisplay(' + repr(data) + ', raw=True)')
    cell.execution_count = 5
    cell.outputs = [nbformat.v4.new_output('display_data', data=data, metadata={'width': 100})]
    path = saved(tmp_path, cell)
    assert npf.verify(path)['outputComparisons'][0]['status'] == 'matched'
    cell.outputs[0].data['application/json']['value'] = 41
    path = saved(tmp_path, cell)
    assert npf.verify(path)['outputComparisons'][0]['status'] == 'changed'


def test_widget_payloads_do_not_produce_a_false_match(tmp_path):
    data = {'application/vnd.jupyter.widget-view+json': {'model_id': 'unstable'}, 'text/plain': 'Widget'}
    cell = code('from IPython.display import display\ndisplay(' + repr(data) + ', raw=True)')
    cell.execution_count = 1
    cell.outputs = [nbformat.v4.new_output('display_data', data=data)]
    report = npf.verify(saved(tmp_path, cell))
    assert report['outputComparisons'][0]['status'] == 'partial'
    assert report['comparisonComplete'] is False
    assert report['outputsMatch'] is None


def test_missing_baselines_and_unexecuted_cells_are_disclosed(tmp_path):
    path = saved(tmp_path, code('print(2)'), code('raise ValueError("broken")'), code('print(3)', '3\n'))
    report = npf.verify(path)
    assert [c['status'] for c in report['outputComparisons']] == ['no_baseline', 'failed', 'not_run']
    assert report['outputsMatch'] is None
    assert report['failedCell'] == 2


def test_explicit_output_ignore_is_disclosed_but_cell_still_runs(tmp_path):
    path = saved(tmp_path, code('print(2)', '1\n', tags=['notebookproof-ignore-output']))
    report = npf.verify(path)
    assert report['succeeded'] is True
    assert report['outputComparisons'][0]['status'] == 'ignored'
    assert report['comparisonComplete'] is False


def test_one_command_combines_static_and_fresh_evidence(tmp_path):
    path = saved(tmp_path, code('print(2)', '1\n'))
    report = npf.check(path, run=True)
    assert report['execution']['succeeded'] is True
    assert any(f['code'] == 'OUTPUT_CHANGED' and f['cell'] == 0 for f in report['findings'])


@pytest.mark.parametrize('operation,status', [('write_text("new contents")', 'changed'), ('unlink()', 'unreadable')])
def test_notebook_side_effects_cannot_hide_a_changed_or_missing_source(tmp_path, operation, status):
    path = saved(tmp_path, code('pass'))
    cell = code('from pathlib import Path\nPath(' + repr(str(path)) + ').' + operation)
    nbformat.write(nbformat.v4.new_notebook(cells=[cell]), path)
    report = npf.check(path, run=True)
    assert report['execution']['sourceStatus'] == status
    assert any(f['code'] == 'SOURCE_CHANGED' for f in report['findings'])
    if status == 'changed':
        assert path.read_text(encoding='utf-8') == 'new contents'
    else:
        assert not path.exists()


def test_resolved_saved_errors_are_distinguished_from_current_failures(tmp_path):
    cell = code('print(2)', '2\n')
    cell.outputs = [nbformat.v4.new_output('error', ename='NameError', evalue='old', traceback=[])]
    report = npf.check(saved(tmp_path, cell), run=True)
    assert next(f for f in report['findings'] if f['code'] == 'SAVED_ERROR')['severity'] == 'info'
    assert not any(f['code'] == 'FRESH_RUN_FAILED' for f in report['findings'])


def test_deferred_missing_names_are_information_until_a_call_fails():
    report = npf.analyze_notebook({'cells': [{'cell_type': 'code', 'source': 'def unused_helper():\n    return supplied_later', 'outputs': []}]})
    assert next(f for f in report['findings'] if f['code'] == 'UNDEFINED')['severity'] == 'info'


def test_fresh_execution_distinguishes_static_parser_limits(monkeypatch, tmp_path):
    def older_parser(source, **kwargs):
        raise SyntaxError('not supported by this parser version')
    monkeypatch.setattr(npf, 'cell_symbols', older_parser)
    report = npf.check(saved(tmp_path, code('print(2)', '2\n')), run=True)
    assert report['execution']['succeeded'] is True
    assert next(f for f in report['findings'] if f['code'] == 'SYNTAX_ERROR')['severity'] == 'info'


def test_combined_check_cannot_recursively_spawn_kernels(tmp_path):
    path = saved(tmp_path, code('pass'))
    cell = code('import notebookproof\nnotebookproof.check(' + repr(str(path)) + ', run=True)')
    nbformat.write(nbformat.v4.new_notebook(cells=[cell]), path)
    report = npf.check(path, run=True)
    assert report['execution']['succeeded'] is True


def test_quiet_default_keeps_dependencies_in_returned_report(tmp_path, capsys):
    report = npf.check(saved(tmp_path, code('value = 1'), code('value + 1')))
    assert report['cells'][1]['reads']
    assert 'writes value' not in capsys.readouterr().out
    npf.check(tmp_path / 'analysis.ipynb', details=True)
    assert 'writes value' in capsys.readouterr().out


@pytest.mark.parametrize('library', ['pandas', 'polars'])
def test_explicit_rules_report_real_violations_without_changing_values(library):
    values = {'id': [1, 1, 2, 3], 'amount': [None, -1, 4, 11]}
    df = pd.DataFrame(values) if library == 'pandas' else pl.DataFrame(values)
    before = df.copy(deep=True) if library == 'pandas' else df.clone()
    report = npf.frame(df, rules={'id': {'unique': True}, 'amount': {'nullable': False, 'min': 0, 'max': 10}})
    findings = {f['code']: f for f in report['findings']}
    assert {'RULE_NULL', 'RULE_UNIQUE', 'RULE_MIN', 'RULE_MAX'} <= findings.keys()
    assert all(findings[c]['severity'] == 'error' for c in ('RULE_NULL', 'RULE_UNIQUE', 'RULE_MIN', 'RULE_MAX'))
    assert report['rulesPassed'] is False
    assert findings['RULE_NULL']['rowPositions'] == [1]
    assert findings['RULE_UNIQUE']['rowPositions'] == [2]
    assert findings['RULE_MIN']['rowPositions'] == [2]
    assert findings['RULE_MAX']['rowPositions'] == [4]
    if library == 'pandas':
        pd.testing.assert_frame_equal(df, before)
    else:
        assert df.equals(before)


def test_expected_nulls_are_observations_and_rule_results_are_not_guessed(capsys):
    report = npf.frame(pd.DataFrame({'value': [None, 1]}), rules={'value': {'nullable': True}})
    assert report['rulesPassed'] is True
    assert all(f['severity'] == 'info' for f in report['findings'] if f['code'] == 'MISSING')
    assert 'WARN' not in capsys.readouterr().out
    assert npf.frame(pd.DataFrame({'x': [1]}))['rulesPassed'] is None


def test_empty_data_cannot_claim_rules_were_verified():
    report = npf.frame(pd.DataFrame({'x': pd.Series(dtype='float64')}), rules={'x': {'min': 0}})
    assert report['rulesPassed'] is None


def test_confirmed_rule_violation_wins_over_an_uncheckable_rule():
    class Unhashable:
        __hash__ = None
    df = pd.DataFrame({'x': [Unhashable(), None]})
    report = npf.frame(df, rules={'x': {'unique': True, 'nullable': False}})
    assert report['rulesPassed'] is False


@pytest.mark.parametrize('has_null', [False, True])
def test_declared_uncheckable_keys_never_claim_success(has_null):
    class Unhashable:
        __hash__ = None
    df = pd.DataFrame({'key': [Unhashable(), None] if has_null else [Unhashable()]})
    report = npf.frame(df, keys=['key'])
    assert report['rulesPassed'] is (False if has_null else None)
    if has_null:
        assert 'MISSING_KEY' in {f['code'] for f in report['findings']}


def test_json_booleans_and_numbers_are_different_results(tmp_path):
    cell = code("from IPython.display import display\ndisplay({'application/json': {'value': True}}, raw=True)")
    cell.execution_count = 1
    cell.outputs = [nbformat.v4.new_output('display_data', data={'application/json': {'value': 1}})]
    assert npf.verify(saved(tmp_path, cell))['outputComparisons'][0]['status'] == 'changed'


def test_range_rules_reject_nonnumeric_values_and_preserve_integer_precision():
    df = pd.DataFrame({'x': pd.Series([2**60, 2**60 + 1, 'wrong'], dtype=object)})
    report = npf.frame(df, rules={'x': {'max': 2**60}})
    assert {'RULE_TYPE', 'RULE_MAX'} <= {f['code'] for f in report['findings']}
    assert next(f for f in report['findings'] if f['code'] == 'RULE_MAX')['count'] == 1


@pytest.mark.parametrize('rules', [[], {'missing': {'min': 0}}, {'x': {'guess': True}},
    {'x': {'nullable': 'yes'}}, {'x': {'min': True}}, {'x': {'min': float('nan')}},
    {'x': {'min': 2, 'max': 1}}])
def test_rules_are_validated_before_inspection(rules):
    with pytest.raises(ValueError):
        npf.frame(pd.DataFrame({'x': [1]}), rules=rules)


def test_local_report_is_escaped_and_does_not_include_raw_data_or_code(tmp_path):
    report = npf.frame(pd.DataFrame({'<script>alert(1)</script>': ['private-value', None]}))
    before = json.dumps(report)
    output = npf.save_report(report, tmp_path / 'report.html')
    text = output.read_text(encoding='utf-8')
    assert '&lt;script&gt;' in text
    assert '<script>' not in text
    assert 'private-value' not in text
    assert 'https://' not in text
    assert json.dumps(report) == before
    with pytest.raises(FileExistsError):
        npf.save_report(report, output)
    with pytest.raises(ValueError):
        npf.save_report(report, tmp_path / 'original.ipynb')
    with pytest.raises(ValueError):
        npf.save_report(report, tmp_path / 'new.html', overwrite='False')


def test_nested_report_export_preserves_existing_html(monkeypatch, tmp_path):
    report = npf.frame(pd.DataFrame({'x': [1]}))
    path = tmp_path / 'report.html'
    path.write_text('original report', encoding='utf-8')
    monkeypatch.setenv('NOTEBOOKPROOF_VERIFY_ACTIVE', '1')
    assert npf.save_report(report, path) == path.resolve()
    assert path.read_text(encoding='utf-8') == 'original report'


def test_shared_table_report_identifies_the_rules_that_passed(tmp_path):
    report = npf.frame(pd.DataFrame({'x': [1, 2]}), keys=['x'], rules={'x': {'min': 0}})
    output = npf.save_report(report, tmp_path / 'report.html').read_text(encoding='utf-8')
    assert 'min=0' in output
    assert 'Unique key: x' in output
    assert 'passed on checked rows' in output


@pytest.mark.parametrize('source,should_warn', [
    ('import random\nrng = random.Random(42)\nvalue = rng.random()', False),
    ('import random\ndef setup():\n    random.seed(42)\nvalue = random.random()', True),
    ('import numpy as np\nnp.random.seed(42)\nrng = np.random.default_rng()\nvalue = rng.random()', True),
    ('import random\nstate = random.getstate()', False),
    ('import random\nrandom.seed(random.random())\nvalue = random.random()', True),
    ('import random\nif False:\n    random.seed(42)\nvalue = random.random()', True),
    ('import random\nrng = random.Random(x=42)\nvalue = rng.random()', False),
    ('import numpy as np\nvalue = np.random.beta(a=2, b=4)', True),
])
def test_seed_analysis_respects_independent_generators_and_execution_scope(source, should_warn):
    report = npf.analyze_notebook({'cells': [{'cell_type': 'code', 'source': source, 'outputs': []}]})
    assert any(f['code'] == 'UNSEEDED_RANDOM' for f in report['findings']) is should_warn
