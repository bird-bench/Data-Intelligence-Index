import json
import sys

import pytest
import yaml

from dii_harbor import run

TASK_TOML = '''[agent]
network_mode = "no-network"
[environment]
network_mode = "no-network"
[environment.env]
OPENAI_API_KEY = ""
'''


def make_tasks(root, names):
    for name in names:
        (root / name).mkdir(parents=True)
        (root / name / 'task.toml').write_text(TASK_TOML)


def invoke(monkeypatch, tmp_path, *argv):
    calls = []
    # subprocess.run is patched module-wide, so the macOS system-proxy lookup must not reach it.
    monkeypatch.setattr('dii_harbor.deployment._macos_http_proxy', lambda: None)
    monkeypatch.setattr(run.subprocess, 'run', lambda cmd, env: calls.append((cmd, env)) or
                        type('R', (), {'returncode': 0})())
    harbor = tmp_path / 'harbor'
    harbor.write_text('#!/bin/sh\n')
    harbor.chmod(0o755)
    monkeypatch.setattr(sys, 'argv', ['run', '--jobs-dir', str(tmp_path / 'jobs'), '--job-name', 'j',
                                      '--harbor-bin', str(harbor), '--provider', 'docker', *argv])
    with pytest.raises(SystemExit) as stop:
        run.main()
    assert stop.value.code == 0
    return calls, yaml.safe_load((tmp_path / 'jobs' / 'j.config.yaml').read_text())


def test_local_tasks_with_v1_defaults(monkeypatch, tmp_path):
    make_tasks(tmp_path / 'tasks', ['dii-a-1', 'dii-a-2', 'dii-b-1'])
    env_file = tmp_path / '.env'
    env_file.write_text('OPENAI_API_KEY=sk-test\n')
    calls, config = invoke(monkeypatch, tmp_path, '--model', 'openai/gpt-x', '--reasoning', 'high',
                           '--tasks-dir', str(tmp_path / 'tasks'), '--tasks', 'dii-a-*',
                           '--env-file', str(env_file))
    assert config['datasets'] == [{'path': str((tmp_path / 'tasks').resolve()), 'task_names': ['dii-a-1', 'dii-a-2']}]
    agent = config['agents'][0]
    assert agent['import_path'] == 'dii_harbor.agents.offline_mini:OfflineMiniSweAgent'
    assert agent['override_timeout_sec'] == 3600
    kwargs = agent['kwargs']
    assert (kwargs['max_tokens'], kwargs['reasoning_effort'], kwargs['config']['agent']['step_limit']) == (16384, 'high', 300)
    assert kwargs['config']['environment']['timeout'] == 180
    cmd, env = calls[0]
    assert cmd[1:3] == ['run', '--config'] and env['OPENAI_API_KEY'] == 'sk-test'
    # Keys never reach files on disk.
    for path in (tmp_path / 'jobs').iterdir():
        assert 'sk-test' not in path.read_text()
    assert json.loads((tmp_path / 'jobs' / 'j.settings.json').read_text())['dii_v1_defaults'] is True


def test_hub_dataset_and_oracle(monkeypatch, tmp_path):
    # Hub task names carry the org; patterns are matched against the public task list.
    _, config = invoke(monkeypatch, tmp_path, '--agent', 'oracle', '--dataset', 'org/dii@v1.0',
                       '--tasks', 'critic-mysql-47', 'workspace-en-100', '--env-file', str(tmp_path / 'missing.env'))
    assert config['datasets'] == [{'name': 'org/dii', 'ref': 'v1.0',
                                   'task_names': ['org/critic-mysql-47', 'org/workspace-en-100']}]
    assert config['agents'][0]['name'] == 'oracle'


def test_workspace_runs_at_65536(monkeypatch, tmp_path):
    calls, config = invoke(monkeypatch, tmp_path, '--model', 'openai/gpt-x', '--tasks', 'critic-mysql-47',
                           'workspace-en-100', '--env-file', str(tmp_path / 'missing.env'))
    assert len(calls) == 2
    assert config['datasets'][0]['task_names'] == ['birdsql/critic-mysql-47']
    assert config['agents'][0]['kwargs']['max_tokens'] == 16384
    ws = yaml.safe_load((tmp_path / 'jobs' / 'j-workspace.config.yaml').read_text())
    assert ws['datasets'][0]['task_names'] == ['birdsql/workspace-en-100']
    assert ws['agents'][0]['kwargs']['max_tokens'] == 65536
    assert json.loads((tmp_path / 'jobs' / 'j-workspace.settings.json').read_text())['dii_v1_defaults'] is True


def test_max_tokens_flag_gives_one_job(monkeypatch, tmp_path):
    calls, config = invoke(monkeypatch, tmp_path, '--model', 'openai/gpt-x', '--max-tokens', '32768',
                           '--tasks', 'critic-mysql-47', 'workspace-en-100', '--env-file', str(tmp_path / 'missing.env'))
    assert len(calls) == 1 and config['agents'][0]['kwargs']['max_tokens'] == 32768
    assert len(config['datasets'][0]['task_names']) == 2


def test_online_task_is_rejected(monkeypatch, tmp_path):
    make_tasks(tmp_path / 'tasks', ['t'])
    (tmp_path / 'tasks' / 't' / 'task.toml').write_text(TASK_TOML.replace('no-network', 'public', 1))
    monkeypatch.setattr(sys, 'argv', ['run', '--agent', 'oracle', '--tasks-dir', str(tmp_path / 'tasks')])
    with pytest.raises(SystemExit) as stop:
        run.main()
    assert stop.value.code == 2
