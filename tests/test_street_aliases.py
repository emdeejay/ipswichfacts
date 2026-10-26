"""Street spellings that differ only in capitalisation or apostrophes share one
page. Synthetic cases; labels reproduce the five observed formatting classes,
not source records."""
import copy
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build import build_site as b
from test_suburb_joins import graph, project

# (page label, source spellings, path) — one pair per observed collision.
GROUPS = [
    ('Anzac Day Parade', ['ANZAC Day Parade', 'Anzac Day Parade'], 'anzac-day-parade'),
    ("Augustine's Drive", ["Augustine's Drive", 'Augustines Drive'], 'augustines-drive'),
    ('Brisbane Street', ['BRISBANE Street', 'Brisbane Street'], 'brisbane-street'),
    ('Nicholas Street', ['NIcholas Street', 'Nicholas Street'], 'nicholas-street'),
    ('Tallegalla Two Tree Hill Road', ['TallEgAlla Two Tree Hill Road', 'Tallegalla Two Tree Hill Road'],
     'tallegalla-two-tree-hill-road'),
]


def sources(variants):
    """Every source kind, each spelling used by a different record."""
    a, z = variants
    ps = [project(1, 'Synthetic', description=f'Works on {a}.'),
          project(2, 'Synthetic', description=f'Works on {z}.')]
    closures = {'closures': [
        dict(road_name=a, suburb='Synthetic', event_type='Roadworks', impact='Lane closed', description='One'),
        dict(road_name=z, suburb='Synthetic', event_type='Roadworks', impact='Lane closed', description='Two')]}
    meetings = {'meetings': [dict(slug='synthetic-meeting', id='M1', committee='Synthetic', doc_type='MIN',
        date='2026-01-01', items=[dict(anchor='i1', title=f'Item about {a}', text=''),
                                  dict(anchor='i2', title=f'Item about {z}', text='')])]}
    news = {'posts': [dict(slug='news-a', id=1, title=f'{a} update', text='', date='2026-01-02', url='https://example.org/a'),
                      dict(slug='news-z', id=2, title=f'{z} update', text='', date='2026-01-03', url='https://example.org/z')]}
    cons = [dict(slug='cons-a', id=1, name=f'Have your say on {a}', suburbs=[], status='Open', date='2026-01-04',
                 url='https://example.org/ca'),
            dict(slug='cons-z', id=2, name=f'Have your say on {z}', suburbs=[], status='Closed', date='2026-01-05',
                 url='https://example.org/cz')]
    das = [dict(application_number='SYN/1', suburb='Synthetic', description=f'Dwelling at {a}',
                date_received='2026-01-06', source_url='https://example.org/da1'),
           dict(application_number='SYN/2', suburb='Synthetic', description=f'Dwelling at {z}',
                date_received='2026-01-07', source_url='https://example.org/da2')]
    return ps, closures, meetings, news, cons, das


def build(tmp_path, ps, closures, meetings, news, cons, das, name='out'):
    g = graph(ps, closures, meetings, news, cons, das)
    out = tmp_path / name
    urls = b.write_site(out, ps, closures, meetings, news, g, [], cons, das)
    return g, out, urls


def article(html):
    return html.split('<article>', 1)[1].split('</article>', 1)[0]


@pytest.mark.parametrize('label,variants,path', GROUPS)
def test_observed_groups_share_one_page_with_every_reference(label, variants, path, tmp_path):
    srcs = sources(variants)
    ps, closures, _, _, cons, das = srcs
    raw = copy.deepcopy((ps, closures, cons, das))
    identities = [b._project_registry_key(p) for p in ps]
    g, out, urls = build(tmp_path, *srcs)
    assert (ps, closures, cons, das) == raw
    assert [b._project_registry_key(p) for p in ps] == identities
    assert g['streets'] == [label]
    assert json.loads((out / 'data/streets.json').read_text()) == [label]
    assert urls.count((f'/street/{path}/', None)) == 1
    assert sorted((out / 'street').iterdir()) == [out / 'street' / path]
    sitemap = (out / 'sitemap.xml').read_text()
    assert sitemap.count(f'/street/{path}/<') == 1

    body = article((out / f'street/{path}/index.html').read_text())
    for href in ['/project/synthetic-1/', '/project/synthetic-2/', '/news/news-a/', '/news/news-z/',
                 '/meeting/synthetic-meeting/#i1', '/meeting/synthetic-meeting/#i2',
                 '/consultation/cons-a/', '/consultation/cons-z/',
                 'https://example.org/da1', 'https://example.org/da2']:
        assert body.count(f'href="{href}"') >= 1, href
    assert body.count('<td>One</td>') == 1 and body.count('<td>Two</td>') == 1
    # Each spelling is named once, and the page says why they share it.
    assert 'differ only in capitalisation or apostrophes' in body
    for v in variants:
        assert body.count(f'<q>{b.h(v)}</q>') == 1
    # No page links to itself as a connected street.
    assert 'Connected streets' not in body


def test_source_spellings_are_kept_on_the_records_that_wrote_them(tmp_path):
    srcs = sources(['BRISBANE Street', 'Brisbane Street'])
    g, out, _ = build(tmp_path, *srcs)
    mentions = json.loads((out / 'data/mentions.json').read_text())
    assert mentions['project_streets'] == {'synthetic-1': ['BRISBANE Street'], 'synthetic-2': ['Brisbane Street']}
    assert mentions['closure_streets'] == {'c0': ['BRISBANE Street'], 'c1': ['Brisbane Street']}
    assert g['street_variants'] == {'brisbane street': ['BRISBANE Street', 'Brisbane Street']}
    p1 = (out / 'project/synthetic-1/index.html').read_text()
    assert '<a href="/street/brisbane-street/">BRISBANE Street</a>' in p1
    news = (out / 'news/news-a/index.html').read_text()
    assert '<a href="/street/brisbane-street/">BRISBANE Street</a>' in news


def test_item_naming_both_spellings_is_listed_once():
    meetings = {'meetings': [dict(slug='m', committee='Synthetic', doc_type='MIN', date='2026-01-01',
        items=[dict(anchor='x', title='NIcholas Street and Nicholas Street', text='')])]}
    news = {'posts': [dict(slug='n', title='NIcholas Street, Nicholas Street', text='', date='2026-01-01')]}
    das = [dict(application_number='SYN/9', suburb='Synthetic', description='NIcholas Street / Nicholas Street')]
    g = graph(meetings=meetings, news=news, das=das)
    assert [r['anchor'] for r in g['street_meeting_items']['Nicholas Street']] == ['x']
    assert [r['slug'] for r in g['street_news_items']['Nicholas Street']] == ['n']
    assert [r['application_number'] for r in g['street_das']['Nicholas Street']] == ['SYN/9']
    # The item itself still records exactly what its text said.
    assert meetings['meetings'][0]['items'][0]['streets'] == ['NIcholas Street', 'Nicholas Street']


def test_output_is_identical_under_input_order_changes(tmp_path):
    ps, closures, meetings, news, cons, das = sources(['TallEgAlla Two Tree Hill Road', 'Tallegalla Two Tree Hill Road'])
    _, out1, urls1 = build(tmp_path, ps, closures, meetings, news, cons, das, 'one')
    rev = (list(reversed(ps)), {'closures': list(reversed(closures['closures']))},
           {'meetings': [dict(meetings['meetings'][0], items=list(reversed(meetings['meetings'][0]['items'])))]},
           {'posts': list(reversed(news['posts']))}, list(reversed(cons)), list(reversed(das)))
    g2, out2, urls2 = build(tmp_path, *copy.deepcopy(rev), name='two')
    path = 'street/tallegalla-two-tree-hill-road/index.html'
    # Project and road-impact rows follow the feed's own order, as on every
    # street page; compared as row sets. Everything else is byte-identical.
    def rows_as_sets(s):
        return re.sub(r'<tbody>(.*?)</tbody>',
                      lambda m: '<tbody>' + ''.join(sorted(m.group(1).split('</tr>'))) + '</tbody>', s, flags=re.S)
    assert rows_as_sets((out1 / path).read_text()) == rows_as_sets((out2 / path).read_text())
    for f in ['data/streets.json', 'streets/index.html']:
        assert (out1 / f).read_bytes() == (out2 / f).read_bytes()
    assert sorted(urls1) == sorted(urls2)
    assert g2['street_labels'] == {'tallegalla two tree hill road': 'Tallegalla Two Tree Hill Road'}


def test_connected_streets_and_project_links_follow_the_shared_page():
    ps = [project(1, 'Synthetic', description='BRISBANE Street at Gordon Street.'),
          project(2, 'Synthetic', description='Brisbane Street and BRISBANE Street.')]
    closures = {'closures': [dict(road_name='Brisbane Street', suburb='Synthetic', status='Current',
                                  start_time='2026-01-01T00:00:00')]}
    meetings = {'meetings': [dict(slug='m', committee='Synthetic', doc_type='MIN', date='2026-01-01',
        items=[dict(anchor='x', title='Brisbane Street report', text='')])]}
    g = graph(ps, closures, meetings)
    assert g['related_streets'] == {'Brisbane Street': ['Gordon Street'], 'Gordon Street': ['Brisbane Street']}
    # A project naming one spelling reaches items filed under the other.
    page = b.render_project(ps[0], closures, g)
    assert 'Brisbane Street report' in page
    assert any(e['kind'] == 'closure' for e in b.build_project_timeline(ps[0], g, closures))


def test_distinct_spellings_on_one_path_still_fail_before_touching_output(tmp_path):
    ps = [project(1, 'Synthetic', description='Works on Brisbane Street.')]
    closures = {'closures': [dict(road_name='Brisbane-Street', suburb='Synthetic')]}
    g = graph(ps, closures)
    assert sorted(g['streets']) == ['Brisbane Street', 'Brisbane-Street']
    out = tmp_path / 'out'
    out.mkdir()
    (out / 'sentinel').write_text('keep')
    with pytest.raises(ValueError, match='/street/brisbane-street/'):
        b.write_site(out, ps, closures, {}, {}, g, [], [], [])
    assert list(out.iterdir()) == [out / 'sentinel']


def test_one_spelling_with_two_paths_is_refused_not_guessed(tmp_path):
    closures = {'closures': [dict(road_name='Straße Road', suburb='Synthetic'),
                             dict(road_name='STRASSE Road', suburb='Synthetic')]}
    g = graph(closures=closures)
    out = tmp_path / 'out'
    out.mkdir()
    (out / 'sentinel').write_text('keep')
    with pytest.raises(ValueError, match='Conflicting street paths'):
        b.write_site(out, [], closures, {}, {}, g, [], [], [])
    assert list(out.iterdir()) == [out / 'sentinel']


def test_other_differences_are_not_aliased():
    for a, z in [('Brisbane Street', 'Brisbane Road'), ('Nicholas Street', 'Nicholls Street'),
                 ('Anzac Avenue', 'Anzac Day Parade')]:
        g = graph([project(1, 'S', description=f'{a} and {z}.')])
        assert g['streets'] == sorted([a, z])


def test_single_spelling_pages_carry_no_alias_note():
    ps = [project(1, 'Synthetic', description='Works on Gordon Street.')]
    g = graph(ps)
    assert g['street_variants'] == {'gordon street': ['Gordon Street']}
    assert 'differ only in capitalisation' not in b.render_street('Gordon Street', ps, {}, g)


def test_search_finds_a_shared_page_by_apostrophe_free_spelling():
    js = b._WIDGET_JS
    assert "kind: 'street'" in js and "s.replace(/['\\u2019]/g, '')" in js
