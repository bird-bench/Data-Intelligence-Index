#!/usr/bin/env python3
"""Compute the DII v1.0 Muscle index from Harbor job folders (Python standard library only).

  python -m dii_harbor.score --run my-model=jobs/my-job [--run other=jobs/other] [--cells-out cells.json]

Cell score per (run, task), taking the latest trial that has a score:
  dare, workspace    verifier reward (continuous 0-1)
  effisql            R-VES from <trial>/verifier/result.json (see rves())
  everything else    verifier reward (0 or 1)
  AgentTimeoutError  counts: the verifier reward if it ran, else 0
  infra failures     excluded from the denominator (rerun them): RewardFileNotFoundError, ApiRateLimitError,
                     CancelledError, NonZeroAgentExitCodeError, Docker compose failures, any exception without a reward,
                     and grader infra errors: verifier/result.json says infrastructure_error true (the grader could
                     not run, e.g. its database was missing or the gold query timed out)
Bench score = 100 * mean of the bench's scored cells; category score = mean of its bench scores;
INDEX = mean of the 7 category scores; MICRO = mean of all cells. A run is complete when every task has a score.
A folder that holds several model settings (model, reasoning effort) gives one row per setting."""
import argparse, json, math, os, statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / 'data'
BENCHES = ['critic', 'critic15', 'dare', 'dsct', 'effisql', 'interact', 'livesqlbench', 'minidev', 'workspace']
CATEGORIES = {
    'Database Question Answering': ['minidev'],
    'Database Analysis & Operations': ['livesqlbench'],
    'DB Application Debugging': ['critic', 'critic15'],
    'Human-centric Interaction': ['interact'],
    'Data Science': ['dsct', 'dare'],
    'Query Optimization': ['effisql'],
    'Computer-Use': ['workspace'],
}
INFRA = ('RewardFileNotFoundError', 'ApiRateLimitError', 'CancelledError', 'NonZeroAgentExitCodeError')


def num(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def rves(trial_dir, reward):
    """m * tier(speedup); m = result match and not an explicit is_pass false."""
    p = Path(trial_dir) / 'verifier' / 'result.json'
    if p.exists():
        try:
            x = json.loads(p.read_text())
            m = x.get('is_match') in (True, 'true') and x.get('is_pass') in (True, 'true', None)
            t = num(x.get('speedup')) or 0.0
            return (1.0 if t >= 2 else 0.75 if t >= 1.2 else 0.5 if t >= 1 else 0.25 if t >= 0.5 else 0.0) if m else 0.0
        except (OSError, ValueError):
            pass
    if reward is None: return None
    # Native reward 1 means match with speedup > 1.2, so without the details it is at least the 0.75 tier.
    return 0.75 if reward >= 1 else 0.0


def grader_infra_error(trial_dir):
    """True when the grader itself could not run (it still writes reward 0)."""
    p = Path(trial_dir) / 'verifier' / 'result.json'
    try:
        x = json.loads(p.read_text())
    except (OSError, ValueError):
        return False
    return isinstance(x, dict) and str(x.get('infrastructure_error')).lower() == 'true'


def cell(trial_dir, r, bench):
    info = r.get('exception_info') or {}
    etype = info.get('exception_type') or ''
    emsg = info.get('exception_message') or ''
    vr = r.get('verifier_result') or {}
    rw = vr.get('rewards') if isinstance(vr.get('rewards'), dict) else vr
    reward = num((rw or {}).get('reward'))
    val = rves(trial_dir, reward) if bench == 'effisql' else reward
    if grader_infra_error(trial_dir):
        return None, 'infra:grader'
    if etype == 'AgentTimeoutError':
        return (val if val is not None else 0.0), 'timeout'
    if etype.startswith(INFRA) or 'Docker compose command failed' in emsg or (etype and val is None):
        return None, 'infra:' + etype
    if val is None:
        return None, 'no_reward'
    return max(0.0, val), 'ok'


def setting(r):
    """(model, reasoning effort) of a trial; one jobs folder can hold several (run.py --models)."""
    agent = (r.get('config') or {}).get('agent') or {}
    kwargs = agent.get('kwargs') or {}
    return str(agent.get('model_name') or agent.get('name') or ''), str(kwargs.get('reasoning_effort') or '')


def collect(root, manifest):
    """{setting: {task: (score, status, trial_dir)}} for one run directory, and the names of other tasks."""
    best, ignored = {}, set()
    for d, dirs, files in os.walk(root, followlinks=True):
        if 'result.json' not in files: continue
        try: r = json.loads(Path(d, 'result.json').read_text())
        except (OSError, ValueError): continue
        if not isinstance(r, dict) or 'task_name' not in r and 'verifier_result' not in r: continue
        # task_name may carry a dataset prefix ("<org>/<task>")
        tid = r.get('task_id') if isinstance(r.get('task_id'), dict) else {}
        names = [str(n).rstrip('/').split('/')[-1] for n in (r.get('task_name'), tid.get('path'), os.path.basename(d).split('__')[0]) if n]
        task = next((n for n in names if n in manifest), None)
        if task is None: ignored.add(names[0] if names else d); continue
        score, status = cell(d, r, manifest[task]['bench'])
        key = (score is not None, r.get('finished_at') or r.get('started_at') or '')
        cells = best.setdefault(setting(r), {})
        if task not in cells or key > cells[task][0]:
            cells[task] = (key, score, status, d)
    return {s: {t: v[1:] for t, v in cells.items()} for s, cells in best.items()}, ignored


def aggregate(cells, manifest):
    """cells: {task: score or None} -> {bench: pct, category: pct, INDEX, MICRO, n, expected}."""
    per = {b: [] for b in BENCHES}
    for t, v in cells.items():
        if v is not None: per[manifest[t]['bench']].append(v)
    out = {b: (100 * statistics.mean(per[b]) if per[b] else None) for b in BENCHES}
    for cat, benches in CATEGORIES.items():
        vals = [out[b] for b in benches]
        out[cat] = statistics.mean(vals) if all(v is not None for v in vals) else None
    cats = [out[c] for c in CATEGORIES]
    out['INDEX'] = statistics.mean(cats) if all(v is not None for v in cats) else None
    allv = [v for b in BENCHES for v in per[b]]
    out['MICRO'] = 100 * statistics.mean(allv) if allv else None
    out['n'] = len(allv); out['expected'] = len(manifest)
    return out


def fmt(v): return f'{v:6.1f}' if isinstance(v, (int, float)) else '     -'


def table(rows):
    w = max([18] + [len(name) + 1 for name in rows])
    print(f"{'run':{w}s}" + ''.join(f'{b[:8]:>9s}' for b in BENCHES) + f"{'INDEX':>8s}{'MICRO':>8s}{'cells':>9s}")
    for name, s in sorted(rows.items(), key=lambda kv: -(kv[1]['INDEX'] if kv[1]['INDEX'] is not None else -1)):
        n = f"{s['n']}/{s['expected']}" if 'n' in s else ''
        print(f'{name:{w}s}' + ''.join(f'{fmt(s[b]):>9s}' for b in BENCHES) + f"{fmt(s['INDEX']):>8s}{fmt(s['MICRO']):>8s}{n:>9s}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--manifest', type=Path, default=DATA / 'public200.json', help='task list with each task\'s bench')
    ap.add_argument('--run', action='append', default=[], metavar='NAME=DIR', help='Harbor jobs folder (repeatable); one row per model setting found in it')
    ap.add_argument('--cells-out', type=Path, help='write per-cell scores and statuses as JSON')
    a = ap.parse_args()
    manifest = json.loads(a.manifest.read_text())['tasks']
    if not a.run: ap.error('give at least one --run NAME=DIR')
    rows, dump = {}, {}
    for spec in a.run:
        name, _, root = spec.partition('=')
        if not root or not Path(root).is_dir(): ap.error(f'bad --run {spec!r}')
        groups, ignored = collect(root, manifest)
        if ignored: print(f'[{name}] other tasks ignored: {len(ignored)}')
        for (model, effort), got in sorted(groups.items() or [(('', ''), {})]):
            # Never mix model settings in one row: a folder with several gets one row per setting.
            label = name if len(groups) <= 1 else f"{name}[{model}{'@' + effort if effort else ''}]"
            rows[label] = aggregate({t: v[0] for t, v in got.items()}, manifest)
            missing = [t for t in manifest if t not in got]
            excluded = {t: v[1] for t, v in got.items() if v[0] is None}
            print(f'[{label}] trials matched {len(got)}/{len(manifest)}, missing {len(missing)}, excluded (infra / no reward) {len(excluded)}')
            for t, why in sorted(excluded.items())[:20]: print(f'    excluded {t}: {why}')
            dump[label] = {t: {'score': v[0], 'status': v[1], 'trial_dir': v[2]} for t, v in got.items()}
            dump[label]['_missing'] = missing
    print(); table(rows)
    if a.cells_out: a.cells_out.write_text(json.dumps(dump, indent=1))


if __name__ == '__main__':
    main()
