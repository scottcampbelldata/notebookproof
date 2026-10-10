import json
from pathlib import Path

import pytest

import notebookproof as npf


def save_notebook(tmp_path, source):
    path = tmp_path / 'example.ipynb'
    path.write_text(json.dumps({
        'nbformat': 4, 'nbformat_minor': 5, 'metadata': {},
        'cells': [{'id': 'example', 'cell_type': 'code', 'metadata': {},
                   'source': source, 'execution_count': None, 'outputs': []}],
    }), encoding='utf-8')
    return path


def test_fresh_kernel_success_preserves_original(tmp_path):
    path = save_notebook(tmp_path, 'value = 2 + 2\nassert value == 4')
    before = path.read_bytes()
    report = npf.verify(path)
    assert report['executed'] is True and report['succeeded'] is True
    assert report['outputsCompared'] is False
    assert path.read_bytes() == before


def test_fresh_kernel_failure_reports_cell(tmp_path):
    path = save_notebook(tmp_path, 'print(missing_value)')
    report = npf.verify(path)
    assert report['succeeded'] is False
    assert report['failedCell'] == 1
    assert report['errorName'] == 'NameError'


def test_fresh_kernel_timeout_is_reported(tmp_path):
    path = save_notebook(tmp_path, 'import time\ntime.sleep(5)')
    report = npf.verify(path, timeout=1)
    assert report['succeeded'] is False
    assert report['errorName'] == 'CellTimeoutError'


def test_nested_verification_does_not_spawn_another_kernel(monkeypatch, tmp_path):
    monkeypatch.setenv('NOTEBOOKPROOF_VERIFY_ACTIVE', '1')
    path = save_notebook(tmp_path, 'pass')
    report = npf.verify(path)
    assert report['executed'] is False and report['skipped'] is True


@pytest.mark.parametrize('timeout', [0, -1, True])
def test_verification_rejects_invalid_timeout(tmp_path, timeout):
    with pytest.raises(ValueError):
        npf.verify(save_notebook(tmp_path, 'pass'), timeout=timeout)


def test_quickstart_runs_in_a_real_notebook_kernel():
    path = Path(__file__).resolve().parents[1] / 'examples' / 'quickstart.ipynb'
    before = path.read_bytes()
    report = npf.verify(path)
    assert report['succeeded'] is True
    assert path.read_bytes() == before
