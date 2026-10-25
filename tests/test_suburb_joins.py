"""Synthetic regression cases; labels reproduce formatting classes, not source records."""
import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build import build_site as b

# Synthetic projects exercise all eleven observed label equivalence groups.
GROUPS = [
    ('Augustine Heights', [' Augustine Heights', 'Augustine Heights']),
    ('North Ipswich', [' North Ipswich', 'North Ipswich']),
    *[(s, [s, s + ' ']) for s in ['Blacksoil', 'Blackstone', 'Churchill', 'Ipswich',
                                 'Purga', 'Riverview', 'Rosewood']],
    ('Springfield Lakes', ['Springfield Lakes', 'Springfield Lakes ', 'Springfield lakes']),
    ('Walloon', ['Walloon', 'Walloon ']),
]


def project(i, suburb, **extra):
    return dict(id=i, name=f'Synthetic work {i}', ref=f'SYN-{i}', slug=f'synthetic-{i}',
                suburb=suburb, divisions=[1], **extra)


def graph(projects=(), closures=None, meetings=None, news=None, cons=(), das=()):
    return b.build_graph(list(projects), closures or {}, meetings or {}, news or {}, [], list(cons), list(das))


@pytest.mark.parametrize('label,variants', GROUPS)
def test_collision_groups(label, variants, tmp_path):
    ps = [project(i, s) for i, s in enumerate(variants)]
    g = graph(ps)
    assert g['suburbs'] == [label]
    html = b.render_suburb(label, ps, {}, g)
    assert f'Projects ({len(ps)})' in html
    for p in ps:
        assert html.count(f'href="/project/{p["slug"]}/"') == 1
    urls = b.write_site(tmp_path / 'out', ps, {}, {}, {}, g, [], [], [])
    expected_slug = label.lower().replace(' ', '-')
    assert urls.count((f'/suburb/{expected_slug}/', None)) == 1
    assert json.loads((tmp_path / 'out/data/suburbs.json').read_text()) == [label]
    rg = graph(list(reversed(ps)))
    assert rg['suburbs'] == g['suburbs']


def test_canonical_sources_raw_identity_and_public_shapes(tmp_path):
    ps = [project(1, ' Blackstone '), project(2, 'BLACKSTONE'), project(3, ' iPsWiCh ')]
    closures = {'closures': [dict(suburb='blackstone ', road_name='Synthetic Road')]}
    meetings = {'meetings': [dict(slug='synthetic-meeting', committee='Synthetic', doc_type='MIN', date='2026-01-01',
        items=[dict(anchor='a', title='BLACKSTONE and Blackstone; Ipswich region', text='')])]}
    news = {'posts': [dict(slug='synthetic-news', title='Blackstone update in IPSWICH', text='', date='2026-01-01')]}
    cons = [dict(slug='synthetic-cons', name='Synthetic consultation', suburbs=[' Blackstone', 'BLACKSTONE'],
                 status='Open', date='2026-01-01', url='https://example.org/consultation')]
    das = [dict(application_number='SYN/1', suburb='Blackstone ', description='Synthetic proposal',
                date_received='2026-01-01', source_url='https://example.org/da', project_officer='EXCLUDED')]
    raw = copy.deepcopy((ps, closures, cons, das))
    identities = [b._project_registry_key(p) for p in ps]
    g = graph(ps, closures, meetings, news, cons, das)
    assert g['suburbs'] == ['BLACKSTONE', 'iPsWiCh']  # deterministic choice among clean labels
    for bucket in ['suburb_meeting_items', 'suburb_news_items', 'suburb_consultations', 'suburb_das']:
        assert len(g[bucket]['blackstone']) == 1
        assert not g[bucket].get('ipswich')
    assert g['suburb_divisions'] == {'blackstone': [1], 'ipswich': [1]}
    assert g['closure_suburb_keys'] == {'c0': ['blackstone']}
    assert all(isinstance(v, str) for v in g['project_suburbs'].values())
    assert all(isinstance(v, str) for v in g['closure_suburbs'].values())
    assert g['meeting_suburbs']['synthetic-meeting'] == ['BLACKSTONE']
    assert g['news_suburbs']['synthetic-news'] == ['BLACKSTONE']
    assert (ps, closures, cons, das) == raw
    assert [b._project_registry_key(p) for p in ps] == identities
    out = tmp_path / 'out'
    b.write_site(out, ps, closures, meetings, news, g, [], cons, das)
    mentions = json.loads((out / 'data/mentions.json').read_text())
    assert all(isinstance(v, str) for v in mentions['project_suburbs'].values())
    assert all(isinstance(v, str) for v in mentions['closure_suburbs'].values())
    assert all(isinstance(v, list) for v in mentions['meeting_suburbs'].values())
    assert json.loads((out / 'data/projects.json').read_text()) == ps
    assert json.loads((out / 'data/closures.json').read_text()) == closures
    assert json.loads((out / 'data/consultations.json').read_text())[0]['suburbs'] == cons[0]['suburbs']
    assert json.loads((out / 'data/development_applications.json').read_text())[0]['suburb'] == 'Blackstone '
    assert 'EXCLUDED' not in ''.join(f.read_text() for f in out.rglob('*.json'))
    assert 'Synthetic proposal' in b.render_division(1, ps, closures, g)
    assert 'Active road impacts in Division 1 (1)' in b.render_division(1, ps, closures, g)
    for file in ['project/synthetic-1/index.html', 'meeting/synthetic-meeting/index.html',
                 'news/synthetic-news/index.html', 'consultation/synthetic-cons/index.html',
                 'suburbs/index.html']:
        assert 'href="/suburb/blackstone/"' in (out / file).read_text()
    locs = [e.text for e in ET.parse(out / 'sitemap.xml').getroot().iter()
            if e.tag.endswith('}loc')]
    assert locs and len(locs) == len(set(locs))
    assert locs.count(b.BASE_URL + '/suburb/blackstone/') == 1


def test_whitespace_prose_and_feed_permutation():
    ps = [project(1, 'Springfield Lakes '), project(2, 'Springfield lakes'), project(3, 'Springfield Lakes')]
    news = {'posts': [dict(slug='n', title='SPRINGFIELD\n  LAKES and Springfield Lakes', text='')]}
    a = graph(ps, news=news)
    z = graph(list(reversed(ps)), news=copy.deepcopy(news))
    assert a['suburbs'] == z['suburbs'] == ['Springfield Lakes']
    assert len(a['suburb_news_items']['springfield lakes']) == 1
    assert a['news_suburbs']['n'] == ['Springfield Lakes']


def test_blank_unknown_and_compound_labels(tmp_path):
    labels = [None, '', ' \t\n', 'Wilowbank', 'Willowbank', 'Alpha / Beta', 'Alpha\nBeta']
    ps = [project(i, s) for i, s in enumerate(labels)]
    g = graph(ps)
    assert g['suburbs'] == ['Alpha / Beta', 'Alpha Beta', 'Willowbank', 'Wilowbank']
    assert set(g['suburb_labels']) == {'wilowbank', 'willowbank', 'alpha / beta', 'alpha beta'}
    assert not any('href="/suburb/x/"' in b.render_project(p, {}, g) for p in ps[:3])
    # Atomic punctuation-distinct labels can share a slug: fail rather than migrate either URL.
    with pytest.raises(ValueError, match='suburb.*path|path.*suburb'):
        b.write_site(tmp_path / 'out', ps, {}, {}, {}, g, [], [], [])


def test_registry_copy_unchanged_by_graph_and_render(tmp_path):
    import shutil
    shutil.copyfile('data/url_registry.json', tmp_path / 'url_registry.json')
    reg = b.load_registry(tmp_path)
    ps = [project(1, ' Blackstone '), project(2, 'BLACKSTONE')]
    b.assign_stable_slugs(reg, 'project', ps, b._project_registry_key, lambda p: p['name'],
                          hint_fn=lambda p: [p.get('suburb')])
    before = copy.deepcopy((reg, ps))
    g = graph(ps)
    b.render_suburb('Blackstone', ps, {}, g)
    assert (reg, ps) == before


@pytest.mark.skipif(shutil.which('node') is None, reason='Node is required to execute generated search JavaScript')
def test_generated_search_resolves_raw_and_unicode_labels(tmp_path):
    ps = [project(1, ' Blackstone '), project(2, 'Écho')]
    das = [dict(application_number='SYN/1', suburb='BLACKSTONE ', description='Synthetic'),
           dict(application_number='SYN/2', suburb='Écho', description='Synthetic'),
           dict(application_number='SYN/3', suburb='  ', description='Synthetic')]
    g = graph(ps, das=das)
    out = tmp_path / 'out'
    b.write_site(out, ps, {}, {}, {}, g, [], [], das)
    js = (out / 'js/widget.js').read_text()
    functions = js[js.index('function slugify(s)'):js.index('function mountSearch(')]
    data = {name: json.loads((out / f'data/{name}.json').read_text())
            for name in ['projects', 'streets', 'suburbs', 'meetings', 'news', 'consultations']}
    data['das'] = json.loads((out / 'data/development_applications.json').read_text())
    result = subprocess.run(['node', '-e', functions + '\nconsole.log(JSON.stringify(buildIndex(' +
                             json.dumps(data) + ')));'], check=True, capture_output=True, text=True)
    items = json.loads(result.stdout)
    das = [i for i in items if i['kind'] == 'DA']
    assert [i['href'] for i in das] == ['/suburb/blackstone/', '/suburb/écho/']
    for item in items:
        if item['kind'] in ['DA', 'suburb']:
            assert (out / item['href'].strip('/') / 'index.html').exists()


def test_casefold_contract_and_conflicting_existing_paths(tmp_path):
    assert b._suburb_key('  STRAẞE\n FIELD ') == b._suburb_key('Straße Field') == 'strasse field'
    ps = [project(1, 'Straße'), project(2, 'STRASSE')]
    g = graph(ps)
    assert len(g['suburbs']) == 1
    with pytest.raises(ValueError, match='Conflicting suburb paths'):
        b.write_site(tmp_path / 'out', ps, {}, {}, {}, g, [], [], [])


def test_duplicate_source_references_are_counted_once():
    ps = [project(1, 'Blackstone'), project(2, 'BLACKSTONE')]
    meet = dict(slug='synthetic-meet', items=[dict(title='Blackstone', anchor='a'),
                                           dict(title='BLACKSTONE', anchor='a')])
    news = dict(slug='synthetic-news', title='Blackstone')
    con = dict(slug='synthetic-con', suburbs=['Blackstone', 'BLACKSTONE'])
    da = dict(application_number='SYN/1', suburb='BLACKSTONE')
    g = graph(ps, meetings={'meetings': [meet]}, news={'posts': [news, news.copy()]},
              cons=[con, con.copy()], das=[da, da.copy()])
    for name in ['suburb_meeting_items', 'suburb_news_items', 'suburb_consultations', 'suburb_das']:
        assert len(g[name]['blackstone']) == 1
    html = b.render_suburb('Blackstone', [*ps, ps[0].copy()], {}, g)
    assert 'Projects (2)' in html
