"""Run DII v1.0 Muscle tasks with the reference agent setup.

  python -m dii_harbor.run --model openai/gpt-6-astra --reasoning high
  python -m dii_harbor.run --model anthropic/claude-opus-5.5 --tasks 'critic-*'
  python -m dii_harbor.run --agent oracle --tasks-dir ./tasks --tasks dare-class-if-710f2dd66a09

The agent is mini-swe-agent 2.4.6. It runs on the host and sends each command to the
offline task container through Harbor; only the host process talks to the model API.
Defaults are the DII v1.0 Muscle settings: 300 model calls, 3600 s per task, 180 s per command,
16,384 output tokens per call (65,536 for Workspace-Bench tasks). Leaderboard results must use them.
With the default token limits, Workspace-Bench tasks run as a second Harbor job, <job>-workspace,
after the other tasks.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from fnmatch import fnmatch
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib

import yaml
from dotenv import dotenv_values

from dii_harbor.deployment import launch_settings
from dii_harbor.model_network import endpoint_overrides
from dii_harbor.task_network import require_offline_tasks

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = 'birdsql/dii-v1-muscle'
PUBLIC_TASKS = Path(__file__).resolve().parent / 'data' / 'public200.json'
# Output-token limit per model call, thinking included. Workspace tasks write long documents.
MAX_TOKENS = 16384
WORKSPACE_MAX_TOKENS = 65536
# Model keys the host agent may need. Values are read from the env file and never written to disk.
KEY_NAMES = ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'OPENROUTER_API_KEY', 'GEMINI_API_KEY',
             'DEEPSEEK_API_KEY', 'XAI_API_KEY', 'MISTRAL_API_KEY', 'TOGETHERAI_API_KEY')


def mini_config(steps=300, reminder_rounds=0):
    observation = '''{% if output.output | length <= 20000 %}
{"returncode": {{ output.returncode }}, "output": {{ output.output | tojson }}{% if output.exception_info %}, "exception_info": {{ output.exception_info | tojson }}{% endif %}}
{% else %}
{"returncode": {{ output.returncode }}, "output_head": {{ output.output[:10000] | tojson }}, "output_tail": {{ output.output[-10000:] | tojson }}, "elided_chars": {{ output.output | length - 20000 }}, "warning": "Output truncated. Read smaller ranges or search the relevant file to inspect omitted content."{% if output.exception_info %}, "exception_info": {{ output.exception_info | tojson }}{% endif %}}
{% endif %}'''
    if reminder_rounds:
        observation += '''
{% if step_limit > 0 and step_limit - n_model_calls <= REMINDER_ROUNDS %}
You have {{ step_limit - n_model_calls }} model calls remaining. Complete the required submission before finishing.
{% endif %}'''.replace('REMINDER_ROUNDS', str(reminder_rounds))
    return {
        'agent': {
            'system_template': 'You are a data assistant working in a terminal. Complete the user request using the task environment.',
            'instance_template': '{{task}}\n\nUse bash tools to work. Each command runs in a new shell. '
                'When all required files or user-service submissions are complete, finish with a separate command: '
                '`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`. You cannot continue after that command.',
            'step_limit': steps,
        },
        'environment': {'timeout': 180, 'cwd': '/app'},
        'model': {'model_class': 'litellm_response', 'model_kwargs': {'store': False},
            'observation_template': observation},
    }


def select(names, patterns, where):
    if patterns:
        # Hub task names carry the org ("birdsql/critic-mysql-47"); match patterns with or without it.
        names = [n for n in names if any(fnmatch(n, p.split('/')[-1]) for p in patterns)]
    if not names:
        raise ValueError(f'No tasks in {where} match {patterns or "*"}')
    return names


def local_tasks(tasks_dir, patterns):
    """{task name: benchmark} for local task folders."""
    names = sorted(p.name for p in tasks_dir.iterdir() if (p / 'task.toml').is_file())
    names = select(names, patterns, tasks_dir)
    benches = {}
    for n in names:
        meta = tomllib.loads((tasks_dir / n / 'task.toml').read_text()).get('metadata', {})
        benches[n] = meta.get('benchmark') or n.split('-')[0]
    return benches


def hub_tasks(patterns):
    """{task name: benchmark} for the published tasks, from the public task list."""
    tasks = json.loads(PUBLIC_TASKS.read_text())['tasks']
    return {n: tasks[n]['bench'] for n in select(sorted(tasks), patterns, 'the public task list')}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--agent', choices=['mini-swe-agent', 'oracle'], default='mini-swe-agent',
                        help='oracle runs each task\'s reference solution (a setup check)')
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--dataset', default=DEFAULT_DATASET,
                        help=f'Harbor Hub dataset, NAME or NAME@TAG (default {DEFAULT_DATASET})')
    source.add_argument('--tasks-dir', type=Path, help='Local folder of task folders instead of the hub dataset')
    parser.add_argument('--tasks', nargs='+', help='Task names or quoted globs; default all tasks')
    models = parser.add_mutually_exclusive_group()
    models.add_argument('--model', help='provider/model, e.g. openai/gpt-6-astra, anthropic/claude-opus-5.5, '
                        'openrouter/anthropic/claude-opus-5.5')
    models.add_argument('--models', nargs='+', help='Run every task with every model')
    parser.add_argument('--reasoning', help='Reasoning effort passed to the model (e.g. low, high, max)')
    parser.add_argument('--steps', type=int, default=300, help='Model-call limit per task (DII v1.0 Muscle: 300)')
    parser.add_argument('--timeout', type=int, default=3600, help='Agent time limit per task in seconds (DII v1.0 Muscle: 3600)')
    parser.add_argument('--max-tokens', type=int,
                        help=f'Output-token limit per model call, thinking included, for every task '
                             f'(DII v1.0 Muscle default: {MAX_TOKENS}, and {WORKSPACE_MAX_TOKENS} for Workspace-Bench)')
    parser.add_argument('--concurrency', type=int, default=2, help='Simultaneous tasks (default 2)')
    parser.add_argument('--provider', choices=['auto', 'local', 'docker'], default='auto',
                        help='local works on Docker Desktop; docker is Harbor\'s stock provider (default auto = local)')
    parser.add_argument('--env-file', type=Path, default=Path('.env'),
                        help='Env file with model API keys and optional base URLs (default ./.env)')
    parser.add_argument('--jobs-dir', type=Path, default=Path('jobs'))
    parser.add_argument('--job-name')
    parser.add_argument('--harbor-bin', type=Path, help='Harbor executable; defaults to harbor on PATH')
    parser.add_argument('--dry-run', action='store_true', help='Write the job config and stop')
    args = parser.parse_args()

    model_names = args.models or ([args.model] if args.model else [])
    if args.agent == 'mini-swe-agent' and not model_names:
        parser.error('give --model (or --models)')
    if args.agent == 'oracle' and model_names:
        parser.error('--agent oracle takes no model')
    if (args.steps < 0 or args.timeout < 1 or args.concurrency < 1 or
            (args.max_tokens is not None and args.max_tokens < 1)):
        parser.error('steps must be >= 0; timeout, max tokens and concurrency must be positive')
    try:
        if args.tasks_dir:
            args.tasks_dir = args.tasks_dir.resolve()
            tasks = local_tasks(args.tasks_dir, args.tasks)
            require_offline_tasks(args.tasks_dir, list(tasks))
            source, full_name = {'path': str(args.tasks_dir)}, (lambda n: n)
        else:
            name, _, tag = args.dataset.partition('@')
            tasks = hub_tasks(args.tasks)
            source = {'name': name, 'ref': tag or 'latest'}
            full_name = lambda n, org=name.split('/')[0]: f'{org}/{n}'
        env, environment, deployment = launch_settings(ROOT, provider=args.provider)
        env.update(endpoint_overrides(args.env_file))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))

    job = args.job_name or f"dii-v1-muscle-{args.agent}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,117}', job):
        parser.error('Job name must be a simple filename, at most 118 characters')
    # One Harbor job per token limit: with the defaults, Workspace tasks get their own job.
    if args.agent == 'mini-swe-agent' and args.max_tokens is None:
        workspace = [n for n, b in tasks.items() if b == 'workspace']
        others = [n for n, b in tasks.items() if b != 'workspace']
        groups = [(job, others, MAX_TOKENS), (f'{job}-workspace', workspace, WORKSPACE_MAX_TOKENS)]
    else:
        groups = [(job, list(tasks), args.max_tokens or MAX_TOKENS)]
    groups = [g for g in groups if g[1]]
    jobs_dir = args.jobs_dir.resolve()
    for job_name, _, _ in groups:
        if (jobs_dir / job_name).exists():
            parser.error(f'{jobs_dir / job_name} exists; choose a new --job-name')

    # Keys come from the env file (or the shell) and are passed only to the Harbor process.
    values = {k: v for k, v in dotenv_values(args.env_file).items() if v} if args.env_file.is_file() else {}
    for key in KEY_NAMES:
        if values.get(key):
            env[key] = values[key]
    if not env.get('OPENAI_API_KEY'):
        print('Note: no OPENAI_API_KEY. Interact (simulated user) and Workspace (judge) tasks need it.',
              file=sys.stderr)
    # PATH first, then next to this Python (a virtual environment used without activating it).
    harbor = args.harbor_bin or shutil.which('harbor') or shutil.which('harbor', path=str(Path(sys.executable).parent))
    if not harbor or not os.access(harbor, os.X_OK):
        parser.error('Harbor executable not found; run "python -m pip install ." in this repository, or use --harbor-bin')

    from harbor.models.job.config import JobConfig
    jobs_dir.mkdir(parents=True, exist_ok=True)
    status = 0
    for job_name, names, max_tokens in groups:
        agents = []
        for model_name in model_names or [None]:
            agent = {'override_timeout_sec': args.timeout}
            if args.agent == 'oracle':
                agent['name'] = 'oracle'
            else:
                agent.update(import_path='dii_harbor.agents.offline_mini:OfflineMiniSweAgent', model_name=model_name,
                             kwargs={'version': '2.4.6', 'config': mini_config(args.steps), 'cost_limit': '0',
                                     'reasoning_effort': args.reasoning, 'max_tokens': max_tokens})
            agents.append(agent)
        dataset = dict(source, task_names=[full_name(n) for n in names])
        config = {'job_name': job_name, 'jobs_dir': str(jobs_dir), 'n_attempts': 1,
                  'n_concurrent_trials': args.concurrency, 'retry': {'max_retries': 0},
                  'agents': agents, 'datasets': [dataset], 'environment': environment}
        JobConfig.model_validate(config)
        path = jobs_dir / f'{job_name}.config.yaml'
        path.write_text(yaml.safe_dump(config, sort_keys=False))
        (jobs_dir / f'{job_name}.settings.json').write_text(json.dumps({
            'agent': args.agent, 'models': model_names, 'reasoning': args.reasoning, 'steps': args.steps,
            'timeout_sec': args.timeout, 'command_timeout_sec': 180, 'max_tokens': max_tokens,
            'dataset': {k: v for k, v in dataset.items() if k != 'task_names'}, 'n_tasks': len(names),
            'deployment': deployment,
            'dii_v1_defaults': (args.steps, args.timeout, args.max_tokens) == (300, 3600, None),
        }, indent=2) + '\n')
        command = [str(harbor), 'run', '--config', str(path), '--yes']
        if args.dry_run:
            command.append('--dry-run')
        print(f'Harbor job: {job_name} ({len(names)} tasks, max tokens {max_tokens})', flush=True)
        status = subprocess.run(command, env=env).returncode or status
    raise SystemExit(status)


if __name__ == '__main__':
    main()
