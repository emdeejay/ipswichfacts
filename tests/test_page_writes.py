"""Synthetic output collisions must never damage the prior output."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build import build_site as b
from test_suburb_joins import graph, project


def test_identical_compatible_writes_emit_once(tmp_path):
    p = project(1, 'Synthetic')
    ps = [p, p.copy()]
    out = tmp_path / 'out'
    urls = b.write_site(out, ps, {}, {}, {}, graph(ps), [], [], [])
    assert urls.count(('/project/synthetic-1/', None)) == 1
    assert (out / 'project/synthetic-1/index.html').exists()


@pytest.mark.parametrize('change', ['body', 'date'])
def test_conflicting_project_writes_preserve_prior_output(tmp_path, monkeypatch, change):
    ps = [project(1, 'Synthetic', updated='20260101'), project(2, 'Synthetic', updated='20260101')]
    ps[1]['slug'] = ps[0]['slug']
    if change == 'date':
        ps[1]['updated'] = '20260102'
        monkeypatch.setattr(b, 'render_project', lambda *a: 'same body')
    out = tmp_path / 'out'
    out.mkdir()
    sentinel = out / 'sentinel'
    sentinel.write_bytes(b'previous successful output\x00')
    with pytest.raises(ValueError, match='synthetic-1') as e:
        b.write_site(out, ps, {}, {}, {}, graph(ps), [], [], [])
    assert '1' in str(e.value) and '2' in str(e.value)
    assert sentinel.read_bytes() == b'previous successful output\x00'
    assert list(out.iterdir()) == [sentinel]


def test_global_guard_covers_non_page_files(tmp_path, monkeypatch):
    monkeypatch.setattr(b, 'INDEXNOW_KEY', 'robots')
    out = tmp_path / 'out'
    out.mkdir()
    (out / 'sentinel').write_text('keep')
    with pytest.raises(ValueError, match='robots.txt'):
        b.write_site(out, [], {}, {}, {}, graph(), [], [], [])
    assert (out / 'sentinel').read_text() == 'keep'


def test_unrelated_suburb_slug_collision_preserves_output(tmp_path):
    ps = [project(1, 'Alpha/Beta'), project(2, 'AlphaBeta')]
    out = tmp_path / 'out'
    out.mkdir()
    (out / 'sentinel').write_text('keep')
    with pytest.raises(ValueError, match='suburb.*path|path.*suburb'):
        b.write_site(out, ps, {}, {}, {}, graph(ps), [], [], [])
    assert (out / 'sentinel').read_text() == 'keep'


def test_resolved_output_targets_and_compatible_dates(tmp_path):
    plan = b._OutputPlan(tmp_path / 'out')
    plan.add('same/index.html', 'body', 'first', None)
    plan.add('other/../same/index.html', 'body', 'second', '2026-01-01')
    assert len(plan.entries) == 1
    plan.publish()
    assert (tmp_path / 'out/same/index.html').read_text() == 'body'


def test_identical_body_with_unknown_then_known_date_has_one_sitemap_entry(tmp_path, monkeypatch):
    ps = [project(1, 'Synthetic'), project(2, 'Synthetic', updated='20260101')]
    ps[1]['slug'] = ps[0]['slug']
    monkeypatch.setattr(b, 'render_project', lambda *a: 'same body')
    urls = b.write_site(tmp_path / 'out', ps, {}, {}, {}, graph(ps), [], [], [])
    assert [u for u in urls if u[0] == '/project/synthetic-1/'] == [('/project/synthetic-1/', '2026-01-01')]
