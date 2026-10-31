"""Append-only local drafts, conflict protection and project isolation."""
import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.academic import WorkingDraftConflict, load_working_draft, save_working_draft


def test_empty_read_creates_nothing_and_revisions_preserve_history(tmp_path):
    root = tmp_path / 'project'
    assert load_working_draft(root) == {'content': '', 'version': 0}
    assert not root.exists()
    assert save_working_draft(root, '首稿：数值1.25仍待核实。', 0)['version'] == 1
    first = root / 'drafts/working-draft/v000000000001.json'
    before = hashlib.sha256(first.read_bytes()).hexdigest()
    assert save_working_draft(root, '修订稿：保留不确定程度。', 1)['version'] == 2
    assert load_working_draft(root) == {'content': '修订稿：保留不确定程度。', 'version': 2}
    assert hashlib.sha256(first.read_bytes()).hexdigest() == before
    # Idempotent save does not generate a redundant history version.
    assert save_working_draft(root, '修订稿：保留不确定程度。', 2)['version'] == 2
    assert len(list(first.parent.glob('v*.json'))) == 2


def test_stale_window_cannot_replace_other_windows_saved_work(tmp_path):
    root = tmp_path / 'project'
    save_working_draft(root, '另一窗口已保存的稿件', 0)
    with pytest.raises(WorkingDraftConflict):
        save_working_draft(root, '本窗口仍保留的编辑', 0)
    assert load_working_draft(root)['content'] == '另一窗口已保存的稿件'
    other = tmp_path / 'other-project'
    assert load_working_draft(other) == {'content': '', 'version': 0}


def test_simultaneous_windows_have_one_winner_without_overwriting(tmp_path):
    def attempt(content):
        try:
            return save_working_draft(tmp_path, content, 0)
        except WorkingDraftConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, ['窗口A稿件', '窗口B稿件']))
    saved = [result for result in results if result is not None]
    assert len(saved) == 1
    assert load_working_draft(tmp_path) == saved[0]
    assert len(list((tmp_path/'drafts/working-draft').glob('v*.json'))) == 1


def test_invalid_input_does_not_create_or_change_a_revision(tmp_path):
    save_working_draft(tmp_path, '保留稿', 0)
    for version in [-1, True, '1']:
        with pytest.raises(ValueError):
            save_working_draft(tmp_path, '新稿', version)
    with pytest.raises(ValueError):
        save_working_draft(tmp_path, '字' * 200001, 1)
    assert load_working_draft(tmp_path) == {'content': '保留稿', 'version': 1}


def test_unpublished_staging_is_not_visible_to_readers(tmp_path, monkeypatch):
    from backend import academic
    save_working_draft(tmp_path, '已发布稿', 0)
    real_link = academic.os.link
    observed = []

    def inspect_before_publication(source, target):
        observed.append(load_working_draft(tmp_path))
        assert source.read_text(encoding='utf-8')
        return real_link(source, target)

    monkeypatch.setattr(academic.os, 'link', inspect_before_publication)
    save_working_draft(tmp_path, '发布中的新稿', 1)
    assert observed == [{'content': '已发布稿', 'version': 1}]
    assert load_working_draft(tmp_path) == {'content': '发布中的新稿', 'version': 2}
    assert len(list((tmp_path/'drafts/working-draft/.staging').glob('*.json'))) == 2
