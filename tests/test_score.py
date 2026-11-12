import json

from dii_harbor import score


def trial(root, name, reward=None, exception=None, finished='2026-11-01T00:00:00', model=None):
    d = root / f'{name}__{model or "abc"}'.replace('/', '-')
    d.mkdir(parents=True)
    result = {'task_name': name, 'finished_at': finished,
              'config': {'agent': {'model_name': model, 'kwargs': {'reasoning_effort': 'high'}}},
              'verifier_result': {'rewards': {'reward': reward}} if reward is not None else None,
              'exception_info': {'exception_type': exception} if exception else None}
    (d / 'result.json').write_text(json.dumps(result))
    return d


def test_cell_rules(tmp_path):
    manifest = {'a': {'bench': 'critic'}, 'b': {'bench': 'critic'}, 'c': {'bench': 'dare'},
                'd': {'bench': 'critic'}, 'e': {'bench': 'effisql'}}
    trial(tmp_path, 'a', 1)
    trial(tmp_path, 'b', exception='AgentTimeoutError')              # timeout without reward counts as 0
    trial(tmp_path, 'c', 0.5)
    trial(tmp_path, 'd', exception='RewardFileNotFoundError')        # infra failure: excluded
    e = trial(tmp_path, 'e', 1)
    (e / 'verifier').mkdir()
    (e / 'verifier' / 'result.json').write_text(json.dumps({'is_match': True, 'speedup': 1.1}))
    groups, ignored = score.collect(tmp_path, manifest)
    (got,) = groups.values()
    assert {t: v[0] for t, v in got.items()} == {'a': 1.0, 'b': 0.0, 'c': 0.5, 'd': None, 'e': 0.5}
    assert not ignored


def test_grader_infra_error_is_excluded(tmp_path):
    manifest = {'a': {'bench': 'minidev'}, 'b': {'bench': 'minidev'}}
    for name, flag in (('a', True), ('b', False)):
        d = trial(tmp_path, name, 0)
        (d / 'verifier').mkdir()
        (d / 'verifier' / 'result.json').write_text(json.dumps({'infrastructure_error': flag}))
    (got,) = score.collect(tmp_path, manifest)[0].values()  # one model setting
    assert got['a'][:2] == (None, 'infra:grader')
    assert got['b'][:2] == (0.0, 'ok')


def test_latest_scored_trial_wins(tmp_path):
    manifest = {'a': {'bench': 'critic'}}
    trial(tmp_path / 'r1', 'a', 0, finished='2026-11-01T00:00:00')
    trial(tmp_path / 'r2', 'a', 1, finished='2026-11-02T00:00:00')
    trial(tmp_path / 'r3', 'a', exception='CancelledError', finished='2026-11-03T00:00:00')
    (got,) = score.collect(tmp_path, manifest)[0].values()  # one model setting
    assert got['a'][0] == 1.0


def test_model_settings_are_not_mixed(tmp_path):
    manifest = {'a': {'bench': 'critic'}}
    trial(tmp_path / 'job', 'a', 1, finished='2026-11-01T00:00:00', model='openai/m1')
    trial(tmp_path / 'job', 'a', 0, finished='2026-11-02T00:00:00', model='openai/m2')
    groups, _ = score.collect(tmp_path, manifest)
    assert {s: got['a'][0] for s, got in groups.items()} == {('openai/m1', 'high'): 1.0, ('openai/m2', 'high'): 0.0}


def test_index_is_mean_of_categories():
    assert sorted(b for benches in score.CATEGORIES.values() for b in benches) == sorted(score.BENCHES)
    manifest = {f't{i}': {'bench': b} for i, b in enumerate(score.BENCHES)}
    cells = {t: (1.0 if info['bench'] in ('critic', 'minidev') else 0.0) for t, info in manifest.items()}
    out = score.aggregate(cells, manifest)
    # minidev is a category alone (100); critic shares DB Application Debugging with critic15 (50).
    assert abs(out['INDEX'] - 150 / len(score.CATEGORIES)) < 1e-9
    assert out['n'] == len(score.BENCHES)


def test_public_manifest():
    data = json.loads((score.DATA / 'public200.json').read_text())
    assert len(data['tasks']) == data['n_tasks'] == 200
    assert {v['bench'] for v in data['tasks'].values()} == set(score.BENCHES)
