#!/usr/bin/env python3
"""Tune Weighted HPA on source-disjoint queries, freeze choices, then evaluate held-out queries.

Each timing process is independent. Validation, reporting and plots are outside
C++ query timers. The output directory retains an immutable executable snapshot.
"""
import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
import random
import shutil
import statistics
import subprocess
import sys
import time

from benchmark import summarize_samples, summarize_batches, ROOT
from preprocessing.io_utils import atomic_text, json_text, sha256
from verification.check import check, close
from verification.formats import read_queries, read_results
from verification.generate import generate

SIZES = [250, 500, 750, 1000, 1500, 2000, 3000, 4000, 6000, 8000, 12000]
WEIGHTS = [1.005, 1.01, 1.02, 1.05]


def percentile(values, p):
    values = sorted(values)
    return values[math.ceil(p * len(values)) - 1] if values else None


def quality_summary(gaps):
    values = [g['gap_percent'] for g in gaps if g['gap_percent'] is not None]
    return {'mean_gap_percent': statistics.mean(values) if values else None,
            'p50_gap_percent': percentile(values, .5), 'p95_gap_percent': percentile(values, .95),
            'max_gap_percent': max(values, default=None),
            'optimal_count': sum(g['optimal'] for g in gaps), 'found_count': len(gaps),
            'undefined_relative_gap_count': len(gaps)-len(values),
            'max_extra_distance_m': max((g['extra_distance_m'] for g in gaps), default=0)}


def quality_pass(q):
    return (q['undefined_relative_gap_count'] == 0 and q['p95_gap_percent'] is not None
            and q['p95_gap_percent'] <= 1 + 1e-7 and q['max_gap_percent'] <= 5 + 1e-7)


def candidates(runs):
    groups = {}
    for r in runs:
        if r.get('ok') and r['algorithm'] == 'hpa' and r['weight'] > 1:
            groups.setdefault((r['size'], r['weight']), []).append(r)
    result = []
    for (size, weight), rows in groups.items():
        if not all(quality_pass(r['quality']) for r in rows):
            continue
        result.append({'size': size, 'weight': weight, 'processes': len(rows),
                       'score_ms': statistics.median(r['mean_ms'] for r in rows),
                       'p95_ms': statistics.median(r['p95_ms'] for r in rows),
                       'index_bytes': rows[0]['hpa']['index_bytes'],
                       'build_ms': statistics.median(r['hpa']['index_build_ms'] for r in rows)})
    return result


def choose(rows):
    """3% tie band around the fastest remaining mean, then deterministic ties."""
    if not rows:
        return None
    fastest = min(r['score_ms'] for r in rows)
    tied = [r for r in rows if r['score_ms'] <= fastest * 1.03]
    return min(tied, key=lambda r: (r['p95_ms'], r['index_bytes'], r['build_ms'], r['size'], r['weight']))


def top_sizes(rows, count=2):
    rows = list(rows); sizes = []
    while rows and len(sizes) < count:
        best = choose(rows); sizes.append(best['size'])
        rows = [r for r in rows if r['size'] != best['size']]
    return sizes


def grouped_samples(path, answers, gap_rows):
    groups = {a['query_id']: a.get('group', 'other') for a in answers['answers']}
    timing, quality = {}, {}
    with path.open() as f:
        for row in csv.DictReader(f):
            timing.setdefault(groups[int(row['query_id'])], []).append(int(row['duration_ns']) / 1e6)
    for gap in gap_rows:
        quality.setdefault(groups[gap['query_id']], []).append(gap)
    return {group: {'mean_ms': statistics.mean(values), 'p50_ms': percentile(values, .5),
                    'p95_ms': percentile(values, .95), 'quality': quality_summary(quality.get(group, []))}
            for group, values in timing.items()}


class Experiment:
    def __init__(self, args):
        self.args = args
        self.output = args.output_dir.resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.binary = self.output / 'busmap-bench'
        shutil.copy2(args.binary, self.binary)
        self.runs = []
        self.started = time.monotonic()
        self.deadline = self.started + args.budget_minutes * 60
        self.tune_deadline = self.started + args.budget_minutes * 45
        self.report = {'schema_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                       'platform': platform.platform(), 'python': sys.version, 'seed': args.seed,
                       'binary_sha256': sha256(self.binary), 'graph_sha256': sha256(args.graph),
                       'graph_text_sha256': sha256(args.graph_text), 'budget_minutes': args.budget_minutes,
                       'objective': 'Median process mean service; <3% ties use p95, index bytes, build, L, w.',
                       'timing': 'Full query including path reconstruction, no result cache. RSS includes retained paths.',
                       'path_storage': 'paths',
                       'runs': self.runs, 'incomplete': [], 'locked': None, 'evaluation_ok': None}

    def save(self):
        self.report['elapsed_seconds'] = time.monotonic() - self.started
        atomic_text(self.output / 'report.json', json_text(self.report))

    def run(self, phase, algorithm, suite, size=0, weight=1, process=0, reps=3, warmup=1,
            threads=1, diagnostics=False):
        deadline = self.tune_deadline if phase in ('coarse', 'fine', 'baseline_tune') else self.deadline
        if time.monotonic() >= deadline:
            self.report['incomplete'].append({'phase': phase, 'algorithm': algorithm, 'size': size,
                                               'weight': weight, 'process': process})
            self.save(); return None
        label = f'{phase}-{algorithm}-L{size:g}-w{weight:g}-p{process}-t{threads}'
        destination = self.output / 'runs' / label
        query_path, answer_path = suite / 'queries.txt', suite / 'answers.json'
        _, queries = read_queries(query_path)
        answers = json.loads(answer_path.read_text())
        command = [str(self.binary), '--graph', str(self.args.graph_text.resolve()), '--queries', str(query_path),
                   '--algorithm', algorithm, '--output-dir', str(destination), '--repetitions', str(reps),
                   '--warmup', str(warmup), '--threads', str(threads), '--queue-capacity', '64']
        if algorithm == 'hpa':
            command += ['--hpa-cluster-size', str(size), '--hpa-weight', str(weight)]
        if diagnostics:
            command += ['--hpa-diagnostics']
        row = {'phase': phase, 'algorithm': algorithm, 'size': size, 'weight': weight,
               'process': process, 'threads': threads, 'directory': str(destination.relative_to(self.output)),
               'command': command, 'ok': False}
        print(label, flush=True)
        try:
            completed = subprocess.run(command, capture_output=True, text=True,
                                       timeout=max(.1, min(300, deadline-time.monotonic())))
            destination.mkdir(parents=True, exist_ok=True)
            (destination / 'process.log').write_text(completed.stdout + completed.stderr)
            if completed.returncode:
                raise ValueError(completed.stderr.strip())
            timing = json.loads((destination / 'timing.json').read_text())
            if (timing['build_type'] != 'Release' or timing['algorithm'] != algorithm
                or timing['graph_sha256'] != self.report['graph_sha256'] or timing['threads'] != threads
                or timing['query_count'] != len(queries) or timing['repetitions'] != reps):
                raise ValueError('Incorrect binary/workload metadata')
            if algorithm == 'hpa' and (timing['hpa']['cluster_size_m'] != size
                                       or timing['hpa']['heuristic_weight'] != weight
                                       or timing['hpa'].get('path_storage') != 'paths'):
                raise ValueError('HPA configuration mismatch')
            first_check = None
            first_result_hash = None
            for repetition in range(1, reps + 1):
                result_path = destination / f'results-{repetition}.txt'
                result_hash = sha256(result_path)
                if first_result_hash is not None and result_hash != first_result_hash:
                    raise ValueError('Result changed across identical repeated queries')
                first_result_hash = result_hash
                result = check(self.args.graph, query_path, answer_path, result_path,
                               'any' if weight > 1 else 'optimal')
                # Independently check the advertised multiplicative weighted bound.
                _, results = read_results(result_path)
                bound_errors = []
                for a in answers['answers']:
                    observed = results[a['query_id']]
                    if a['status'] == 'found' and observed['status'] == 'found':
                        upper = weight * a['distance_m']; actual = observed['distance_m']
                        if actual > upper and not close(actual, upper): bound_errors.append(a['query_id'])
                result['weighted_bound_errors'] = bound_errors
                result['ok'] = result['ok'] and not bound_errors
                atomic_text(destination / f'check-{repetition}.json', json_text(result))
                if not result['ok']:
                    raise ValueError(f'Correctness failed: {result["errors"][:3]}, bound={bound_errors[:3]}')
                if first_check is None: first_check = result
            samples = summarize_samples(destination / 'timings.csv', set(queries), reps)
            if not math.isclose(sum(timing['run_query_total_ms']), samples['total_query_ms'], rel_tol=1e-9, abs_tol=1e-6):
                raise ValueError('Timing sum mismatch')
            row.update(timing)
            row.update(samples)
            row.update(summarize_batches(timing['run_batch_wall_ms'], len(queries), reps))
            row['quality'] = quality_summary(first_check['gaps'])
            row['quality_pass'] = quality_pass(row['quality'])
            row['groups'] = grouped_samples(destination / 'timings.csv', answers, first_check['gaps'])
            row['ok'] = True
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            row['error'] = str(error)
            print(f'  FAILED: {error}', flush=True)
        self.runs.append(row); self.save()
        return row


def render_report(output):
    report = json.loads((output / 'report.json').read_text())
    lines = ['# HPA* benchmark and tuning', '',
             f"Graph: `{report['graph_sha256']}`. Binary: `{report['binary_sha256']}`.", '',
             'Source-disjoint tuning; the existing 1,000 queries are held out. No query result cache.',
             'Service includes full path reconstruction. Index build is separate; process peak RSS includes retained result paths.', '',
             '## Locked choices', '', '```json', json.dumps(report['locked'], indent=2), '```', '',
             '## Held-out results', '',
             '| Algorithm | L (m) | w | Workers | Mean service (ms) | p95 (ms) | Throughput/s | Build (ms) | Index KiB | Peak RSS MiB | p95 gap % | Max gap % |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    groups = {}
    for row in report['runs']:
        if row['phase'] in ('evaluation', 'parallel') and row.get('ok'):
            groups.setdefault((row['algorithm'], row['size'], row['weight'], row['threads']), []).append(row)
    for (algorithm, size, weight, threads), rows in groups.items():
        med = lambda key: statistics.median(r[key] for r in rows)
        h = rows[0].get('hpa') or {}; q = rows[0]['quality']
        lines.append(f"| {algorithm} | {size:g} | {weight:g} | {threads} | {med('mean_ms'):.5f} | {med('p95_ms'):.5f} | {med('throughput_qps'):.1f} | "
                     f"{statistics.median((r.get('hpa') or {}).get('index_build_ms', 0) for r in rows):.3f} | {h.get('index_bytes',0)/1024:.1f} | "
                     f"{med('peak_rss_bytes')/1048576:.2f} | {q['p95_gap_percent']:.6f} | {q['max_gap_percent']:.6f} |")
    lines += ['', f"Evaluation quality/correctness passed: **{report['evaluation_ok']}**.", '', '## Break-even against A*', '']
    baseline = groups.get(('astar', 0, 1, 1), [])
    if baseline:
        base = statistics.median(r['mean_ms'] for r in baseline)
        for key, rows in groups.items():
            if key[0] != 'hpa' or key[3] != 1: continue
            mean = statistics.median(r['mean_ms'] for r in rows)
            prep = statistics.median(r['hpa']['index_build_ms'] for r in rows)
            breakeven = math.ceil(prep / (base-mean)) if mean < base else None
            lines.append(f"- L={key[1]:g}, w={key[2]:g}: {base/mean:.3f}× A* speed; "
                         + (f"index-build break-even ≈ {breakeven} queries." if breakeven else 'no break-even because queries are not faster.'))
    lines += ['', '## Per-distance groups', '', '| Algorithm | L | w | Group | Mean service ms | p95 service ms | p95 gap % | Max gap % |',
              '|---|---:|---:|---|---:|---:|---:|---:|']
    for (algorithm,size,weight,threads),rows in groups.items():
        if threads != 1: continue
        for group in ('short','medium','long'):
            gs = [r['groups'][group] for r in rows]
            lines.append(f"| {algorithm} | {size:g} | {weight:g} | {group} | {statistics.median(g['mean_ms'] for g in gs):.5f} | "
                         f"{statistics.median(g['p95_ms'] for g in gs):.5f} | {gs[0]['quality']['p95_gap_percent']:.6f} | {gs[0]['quality']['max_gap_percent']:.6f} |")
    lines += ['', '## Separate instrumented profiling pass', '',
              '| Configuration | Search µs/query | Reconstruction µs/query | Reconstruction share | Overlay pops/query | Queries growing workspace |',
              '|---|---:|---:|---:|---:|---:|']
    for run in report['runs']:
        if run['phase'] != 'diagnostics' or not run.get('ok'): continue
        with (output / run['directory'] / 'diagnostics.csv').open() as stream:
            samples = list(csv.DictReader(stream))
        # Historical sessions used refinement_ns before paths became the sole implementation.
        for sample in samples:
            sample['reconstruction_ns'] = sample.get('reconstruction_ns', sample.get('refinement_ns', 0))
        count = len(samples)
        totals = {key: sum(int(sample[key]) for sample in samples) for key in
                  ('search_ns','reconstruction_ns','overlay_expanded','workspace_grew')}
        denominator = totals['search_ns'] + totals['reconstruction_ns']
        lines.append(f"| L={run['size']:g}, w={run['weight']:g} | {totals['search_ns']/count/1000:.2f} | "
                     f"{totals['reconstruction_ns']/count/1000:.2f} | {100*totals['reconstruction_ns']/denominator:.1f}% | "
                     f"{totals['overlay_expanded']/count:.1f} | {totals['workspace_grew']} |")
    lines += ['', 'These phase timings include instrumentation overhead and are not substituted for the latency benchmark. Workspace growth is a capacity-growth indicator, not a count of every allocation.', '']
    lines += ['', '## Protocol and limits', '',
              '- Coarse: 1 fresh process, 1 warmup, 3 measured batches. Fine: 3 fresh processes, 1 warmup, 5 measured batches.',
              '- Held-out: 3 fresh processes, 2 warmups, 10 measured batches. Parallel: same locked choices, 4 workers, queue 64.',
              '- Tables use medians across process summaries; the JSON retains individual process and query samples.',
              '- Quality uses unique queries, not repeated timing samples. Every measured result batch was checked.',
              '- The 3% band is a selection rule, not a confidence interval. Host load and thermal conditions remain uncontrolled.',
              '- Tuning and held-out sources are disjoint; both suites come from the same historical graph and are not production traffic.',
              f"- Incomplete jobs: {len(report['incomplete'])}; failed jobs: {sum(not r.get('ok') for r in report['runs'])}.",
              '- Detailed configuration, compiler, workspace memory, phase diagnostics, commands and hashes are retained in report.json and runs/.', '']
    atomic_text(output / 'report.md', '\n'.join(lines))
    with (output / 'configuration_summary.csv').open('w', newline='') as stream:
        fields = ['phase','algorithm','size','weight','threads','process','ok','mean_ms','p50_ms','p95_ms',
                  'throughput_qps','index_build_ms','index_bytes','peak_rss_bytes','p95_gap_percent','max_gap_percent']
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in report['runs']:
            flattened = {**row, **(row.get('hpa') or {}), **row.get('quality', {})}
            writer.writerow({key: flattened.get(key) for key in fields})
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        rows = [r for r in report['runs'] if r['phase']=='coarse' and r.get('ok')]
        fig, axes = plt.subplots(2,2,figsize=(12,8), constrained_layout=True)
        for w in sorted({r['weight'] for r in rows}):
            series = sorted((r for r in rows if r['weight']==w), key=lambda r:r['size'])
            axes[0,0].plot([r['size'] for r in series],[r['mean_ms'] for r in series], marker='.',label=f'w={w:g}')
            axes[1,1].plot([r['size'] for r in series],[r['quality']['max_gap_percent'] for r in series], marker='.',label=f'w={w:g}')
        # The index is independent of weight; one observation per size suffices.
        index_rows = sorted({r['size']: r for r in rows}.values(), key=lambda r:r['size'])
        axes[0,1].plot([r['size'] for r in index_rows],[r['hpa']['index_build_ms'] for r in index_rows],marker='o')
        axes[1,0].plot([r['size'] for r in index_rows],[r['hpa']['index_bytes']/1024 for r in index_rows],marker='o', label='Index KiB')
        for ax,title,ylabel in zip(axes.flat,['Full query latency (tuning)','Dijkstra preprocessing','Retained index memory','Maximum path error (tuning)'],['Mean service (ms)','Build time (ms)','KiB','Gap (%)']):
            ax.set(title=title,xlabel='Cluster side (m)',ylabel=ylabel,xscale='log');ax.grid(alpha=.2)
        baselines = [r for r in report['runs'] if r['phase']=='baseline_tune' and r.get('ok') and r['algorithm']=='astar']
        if baselines:
            axes[0,0].axhline(statistics.median(r['mean_ms'] for r in baselines), color='black', linestyle='--', label='A* baseline')
        for name, value in (report.get('locked') or {}).items():
            if value:
                axes[0,0].scatter([value['size']], [value['score_ms']], marker='*', s=140, zorder=5, label=f'Locked {name}')
        axes[0,0].legend(fontsize=8);axes[1,1].legend()
        fig.savefig(output/'tuning.png',dpi=170);fig.savefig(output/'tuning.svg');plt.close(fig)
    except ImportError:
        atomic_text(output/'plot-unavailable.txt','Install matplotlib and run --render-only OUTPUT to generate figures.\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,default=ROOT/'build/busmap-bench')
    parser.add_argument('--graph',type=Path,default=ROOT/'data/processed/graph.json')
    parser.add_argument('--graph-text',type=Path,default=ROOT/'data/processed/graph.txt')
    parser.add_argument('--heldout',type=Path,default=ROOT/'benchmarks')
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--budget-minutes',type=float,default=60)
    parser.add_argument('--sizes',type=float,nargs='+',default=SIZES)
    parser.add_argument('--weights',type=float,nargs='+',default=WEIGHTS)
    parser.add_argument('--seed',type=int,default=162164)
    parser.add_argument('--render-only',type=Path)
    args=parser.parse_args()
    if args.render_only:
        render_report(args.render_only.resolve());return 0
    if not args.output_dir: parser.error('--output-dir is required')
    if not math.isfinite(args.budget_minutes) or args.budget_minutes<=0: parser.error('positive finite budget required')
    if any(not math.isfinite(v) or v<=0 for v in args.sizes): parser.error('positive finite sizes required')
    if any(not math.isfinite(w) or w<=1 or w>1.05 for w in args.weights):
        parser.error('Weighted HPA tuning requires weights in (1,1.05]')
    args.heldout=args.heldout.resolve()
    experiment=Experiment(args)
    tune=experiment.output/'tune'
    _,heldout_queries=read_queries(args.heldout/'queries.txt')
    excluded={s for s,t in heldout_queries.values()}
    generate(args.graph,tune,seed=args.seed,excluded_sources=excluded)
    _,tune_queries=read_queries(tune/'queries.txt')
    if excluded & {s for s,t in tune_queries.values()}: raise ValueError('Source leakage')
    experiment.report['suites']={name:{'queries_sha256':sha256(path/'queries.txt'),'answers_sha256':sha256(path/'answers.json')}
                                  for name,path in [('tune',tune),('heldout',args.heldout)]}
    experiment.save()
    for algorithm in ('dijkstra','astar'):
        experiment.run('baseline_tune',algorithm,tune)
    grid=sorted({(float(s),float(w)) for s in args.sizes for w in args.weights})
    rng=random.Random(args.seed);rng.shuffle(grid)
    for size,weight in grid:
        experiment.run('coarse','hpa',tune,size,weight)
    coarse=[r for r in experiment.runs if r['phase']=='coarse']
    fine=set()
    for size in top_sizes(candidates(coarse)):
        for factor in (1,.75,.875,1.125,1.25):
            value=min(24000,max(125,math.floor(size*factor/50+.5)*50))
            for weight in args.weights:
                fine.add((value,weight))
    jobs=[(s,w,p) for s,w in sorted(fine) for p in range(3)];rng.shuffle(jobs)
    for size,weight,process in jobs:
        experiment.run('fine','hpa',tune,size,weight,process,reps=5)
    fine_rows=[r for r in experiment.runs if r['phase']=='fine']
    eligible=[r for r in candidates(fine_rows) if r['processes']==3]
    locked={'weighted': choose(eligible)}
    experiment.report['locked']=locked
    atomic_text(experiment.output/'locked.json',json_text({'binary_sha256':experiment.report['binary_sha256'],'choices':locked}))
    experiment.save()
    if not all(locked.values()):
        experiment.report['evaluation_ok']=False;experiment.save();render_report(experiment.output)
        print('Insufficient completed/valid fine runs to lock Weighted HPA.');return 1
    evaluation=[('dijkstra',0,1),('astar',0,1)]+[('hpa',v['size'],v['weight']) for v in locked.values()]
    jobs=[(a,s,w,p) for a,s,w in evaluation for p in range(3)];rng.shuffle(jobs)
    for algorithm,size,weight,process in jobs:
        experiment.run('evaluation',algorithm,args.heldout,size,weight,process,reps=10,warmup=2)
    for algorithm,size,weight in evaluation:
        for process in range(3):
            experiment.run('parallel',algorithm,args.heldout,size,weight,process,reps=5,warmup=1,threads=4)
    for value in locked.values():
        experiment.run('diagnostics','hpa',tune,value['size'],value['weight'],reps=1,diagnostics=True)
    evaluated=[r for r in experiment.runs if r['phase']=='evaluation']
    experiment.report['evaluation_ok']=len(evaluated)==len(evaluation)*3 and all(r.get('ok') and r.get('quality_pass') for r in evaluated)
    experiment.save();render_report(experiment.output)
    print(f"Report: {experiment.output/'report.md'}",flush=True)
    return 0 if experiment.report['evaluation_ok'] else 1


if __name__=='__main__':
    raise SystemExit(main())
