"""Check that task solvers stay offline while task services stay reachable."""
from copy import deepcopy
from pathlib import Path
import tomllib

import yaml


SOLVER_ENV_BLANKS = (
    'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
    'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
    'OPENAI_API_KEY', 'OPENAI_BASE_URL',
)


def is_proxy_name(name):
    return name.lower().endswith('_proxy')


def is_solver_secret(name):
    return (is_proxy_name(name) or name.upper().endswith('_API_KEY')
            or name.upper() in {'OPENAI_BASE_URL', 'OPENAI_API_BASE', 'DII_USER_SIM_API_BASE'})


def _offline_env(values):
    result = {key: '' if is_solver_secret(key) else value for key, value in values.items()}
    result.update(dict.fromkeys(SOLVER_ENV_BLANKS, ''))
    return result


def offline_solver_compose(compose):
    """The expected private-network layout of a task's Compose services."""
    result = deepcopy(compose)
    services = result.get('services', {})
    if set(services) == {'main', 'postgres'}:
        for service in services.values():
            service.pop('network_mode', None)
            service['networks'] = ['default']
        result['networks'] = {'default': {'internal': True}}
    elif set(services) == {'main', 'user', 'workdb'}:
        # The user simulator calls its model API. The solver and its
        # database have no public route. The simulator exposes only task tools.
        for name, service in services.items():
            service.pop('network_mode', None)
            service['networks'] = ['task', 'default'] if name == 'user' else ['task']
        result['networks'] = {'task': {'internal': True}, 'default': {}}
    elif set(services) == {'main'} and set(services['main']) <= {'shm_size'}:
        # Optional override for emulated SQL Server: a larger /dev/shm.
        # It adds no network settings; task.toml keeps the solver offline.
        pass
    else:
        raise ValueError('Unsupported solver Compose services: ' + ', '.join(sorted(services)))
    if 'environment' in services['main']:
        values = services['main']['environment']
        if not isinstance(values, dict):
            raise ValueError('Solver Compose environment must be a mapping')
        services['main']['environment'] = _offline_env(values)
    return result


def require_offline_tasks(tasks_dir, names):
    """Reject solver Internet access before starting a job."""
    for name in names:
        task = Path(tasks_dir) / name
        config = tomllib.loads((task / 'task.toml').read_text())
        environment = config.get('environment', {})
        phase = config.get('agent', {})
        reason = None
        if environment.get('network_mode') != 'no-network':
            reason = 'solver environment allows network access'
        elif phase.get('network_mode') != 'no-network' or phase.get('allowed_hosts'):
            reason = 'agent phase must declare no-network without task-specific API hosts'
        elif any(value for key, value in environment.get('env', {}).items() if is_solver_secret(key)):
            reason = 'solver environment contains a proxy or model API setting'
        compose_path = task / 'environment/docker-compose.yaml'
        if reason is None and compose_path.exists():
            current = yaml.safe_load(compose_path.read_text())
            try:
                expected = offline_solver_compose(current)
            except ValueError as exc:
                reason = str(exc)
            else:
                if current != expected:
                    reason = 'solver Compose does not use the private task network'
        if reason:
            raise ValueError(f'{name}: {reason}. DII tasks must keep the solver offline; '
                             'use the published task files unchanged.')
