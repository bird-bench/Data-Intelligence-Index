"""Harbor network policy with a static Docker fallback for older kernels.

Some Docker Desktop kernels lack Harbor's required nftables support.
They use network=none or a private bridge for task-local services.
Supported kernels use Harbor's egress sidecar and phase policy switching.
"""
import asyncio
from copy import deepcopy
from ipaddress import ip_address
import json
import os
import tempfile
import subprocess
from pathlib import Path

import yaml
from harbor.environments.base import ExecResult, OutputCallback
from harbor.environments.docker.docker import DockerEnvironment
from harbor.models.task.config import NetworkMode, NetworkPolicy

from dii_harbor.task_network import is_solver_secret


class DockerPreflightCleanupError(Exception):
    """Prior task state could not be removed before Docker startup."""


def is_shm_only_override(data):
    """A main-only override that just enlarges /dev/shm and adds no networking."""
    return (isinstance(data, dict) and set(data) == {'services'}
            and set(data['services']) == {'main'}
            and set(data['services']['main'] or {}) == {'shm_size'})


def validate_internal_compose(data):
    """Accept private task services, with API access only for Interact's user service."""
    if not isinstance(data, dict) or set(data) != {'services', 'networks'}:
        raise ValueError('Offline task services require explicit private networks')
    services = data['services']
    if (set(services) == {'main', 'postgres'} and
            data['networks'] == {'default': {'internal': True}}):
        expected = {'main': ['default'], 'postgres': ['default']}
        kind = 'postgres'
    elif (set(services) == {'main', 'user', 'workdb'} and
            data['networks'] == {'task': {'internal': True}, 'default': {}}):
        expected = {'main': ['task'], 'workdb': ['task'], 'user': ['task', 'default']}
        kind = 'interact'
    else:
        raise ValueError('Unsupported offline task service network layout')
    allowed = {'image', 'environment', 'healthcheck', 'depends_on', 'networks',
               'cpus', 'mem_limit', 'command', 'platform', 'build'}
    for name, service in services.items():
        if set(service) - allowed or service.get('networks') != expected[name]:
            raise ValueError('Unsupported offline task service: ' + name)
    platforms = [service.get('platform') for service in services.values()]
    if (any(value is not None for value in platforms) and
            (len(set(platforms)) != 1 or platforms[0] not in {'linux/amd64', 'linux/arm64'})):
        raise ValueError('Explicit task service platforms must match')
    image = services['postgres'].get('image', '') if kind == 'postgres' else ''
    if kind == 'postgres' and '@sha256:' not in image:
        raise ValueError('Verifier database image must be pinned by digest')
    return kind


class LocalDockerEnvironment(DockerEnvironment):
    def __init__(self, *args, **kwargs):
        self._runtime_compose_dir = None
        self._runtime_compose_path = None
        self._native_compose_kind = None
        self._native_services_started = False
        policy = kwargs.get('network_policy') or NetworkPolicy()
        self._native_none = policy.network_mode == NetworkMode.NO_NETWORK
        self._native_none_dir = tempfile.TemporaryDirectory(prefix='dii-network-')
        self._native_none_file = Path(self._native_none_dir.name) / 'network.yaml'
        self._native_none_file.write_text('services:\n  main:\n    network_mode: none\n')
        super().__init__(*args, **kwargs)
        self._native_none = self.network_policy.network_mode == NetworkMode.NO_NETWORK
        self._internal_compose = False
        self._internal_compose_kind = None
        if self._enable_egress_control:
            self._native_none = False
            if self.extra_docker_compose_paths:
                raise ValueError('Native DII network policy does not accept extra Compose overlays')
            original = super()._environment_docker_compose_path
            if original.exists():
                data = yaml.safe_load(original.read_text())
                self._native_compose_kind = validate_internal_compose(data)
                self._write_native_compose(original, data)
            return
        if any(p != self.network_policy for p in self._phase_network_policies):
            raise ValueError('Local DII provider supports only static network policies')
        if self._native_none and self.extra_docker_compose_paths:
            raise ValueError('Isolated DII verifier does not accept extra Compose overlays')
        if self._native_none and self._environment_docker_compose_path.exists():
            data = yaml.safe_load(self._environment_docker_compose_path.read_text())
            if is_shm_only_override(data):
                # Optional /dev/shm override for emulated SQL Server; main keeps network_mode none.
                return
            self._internal_compose_kind = validate_internal_compose(data)
            self._native_none = False
            self._internal_compose = True

    @property
    def _environment_docker_compose_path(self):
        return self._runtime_compose_path or super()._environment_docker_compose_path

    def _write_native_compose(self, original, data):
        """Route main through Harbor without changing task files or sidecars."""
        data = deepcopy(data)
        data['services']['main'].pop('networks')
        private = 'task' if self._native_compose_kind == 'interact' else 'default'
        public = 'default' if self._native_compose_kind == 'interact' else 'api'
        data['networks'].setdefault(public, {})
        data['services'][self._EGRESS_CONTROL_SERVICE_NAME] = {'networks': [private, public]}
        for service in data['services'].values():
            build = service.get('build')
            if isinstance(build, str):
                service['build'] = str((original.parent / build).resolve())
            elif isinstance(build, dict) and 'context' in build:
                build['context'] = str((original.parent / build['context']).resolve())
        self._runtime_compose_dir = tempfile.TemporaryDirectory(prefix='dii-egress-')
        self._runtime_compose_path = Path(self._runtime_compose_dir.name) / 'compose.yaml'
        self._runtime_compose_path.write_text(yaml.safe_dump(data, sort_keys=False))

    @property
    def capabilities(self):
        if self._enable_egress_control:
            return super().capabilities
        return super().capabilities.model_copy(update={'disable_internet': True, 'dynamic_network_policy': False})

    @property
    def _docker_compose_paths(self):
        paths = super()._docker_compose_paths
        return paths + [self._native_none_file] if self._native_none else paths

    def _compose_env_vars(self, include_os_env=True):
        env = super()._compose_env_vars(include_os_env=include_os_env)
        if include_os_env:
            # Task blanks must not disable host downloads or trusted sidecar APIs.
            # Harbor writes the solver's blank values to its separate env overlay.
            env.update({key: value for key, value in os.environ.items()
                        if is_solver_secret(key)})
        return env

    async def _run_docker_compose_command(
        self,
        command: list[str],
        check: bool = True,
        timeout_sec: int | None = None,
        stdin_data: bytes | None = None,
        on_output: OutputCallback | None = None,
    ) -> ExecResult:
        if (getattr(self, '_native_compose_kind', None) and command[0] == 'up'
                and not self._native_services_started
                and not set(command[1:]) & {'main', 'postgres', 'user', 'workdb', self._EGRESS_CONTROL_SERVICE_NAME}):
            # Main's healthcheck may need its database. Bind exact service IPs
            # before starting main, while retaining the startup policy.
            services = ['user', 'workdb'] if self._native_compose_kind == 'interact' else ['postgres']
            await super()._run_docker_compose_command(
                [*command, *services, self._EGRESS_CONTROL_SERVICE_NAME],
                check=True, timeout_sec=timeout_sec, on_output=on_output,
            )
            await self._wait_until_egress_sidecar_ready()
            await self._apply_network_policy(self.network_policy)
            self._native_services_started = True
        preflight = command == ['down', '--remove-orphans']
        if preflight:
            self._native_services_started = False
            # Harbor removes stale containers before startup but omits their
            # anonymous volumes. Keep cleanup within this Compose project.
            command = [*command, '--volumes']
        try:
            result = await super()._run_docker_compose_command(
                command, check=check, timeout_sec=timeout_sec,
                stdin_data=stdin_data, on_output=on_output,
            )
        except Exception as exc:
            if not preflight:
                raise
            # Harbor.start catches RuntimeError and then starts anyway. Use a
            # distinct exception so failed cleanup cannot reuse stale DB state.
            raise DockerPreflightCleanupError(
                'Docker preflight cleanup failed; refusing to reuse prior task state'
            ) from exc
        if preflight and result.return_code != 0:
            raise DockerPreflightCleanupError(
                'Docker preflight cleanup failed; refusing to reuse prior task state'
            )
        return result

    async def start(self, force_build):
        await super().start(force_build)
        await self._wait_for_critic15_frappe_entrypoint()
        if self._enable_egress_control:
            return
        if self._native_none:
            result = await self._run_docker_compose_command(['ps', '-q', 'main'])
            container = (result.stdout or '').strip()
            mode = subprocess.check_output(self._engine_cmd('inspect', container,
                '--format', '{{.HostConfig.NetworkMode}}'), text=True).strip()
            if mode != 'none':
                raise RuntimeError('Task environment does not have Docker network_mode=none')
            # Docker Desktop also exposes inactive kernel tunnel devices, so
            # names alone are not a valid connectivity test.
            check = await self.exec(command="python -c \"import socket; s=socket.socket(); s.settimeout(2); assert s.connect_ex(('1.1.1.1',443)) != 0\"")
            if check.return_code != 0:
                raise RuntimeError('Task environment unexpectedly reached an external network')
        elif self._internal_compose:
            if self._internal_compose_kind == 'interact':
                await self._check_interact_network()
                return
            result = await self._run_docker_compose_command(['ps', '-q'])
            containers = (result.stdout or '').split()
            if len(containers) != 2:
                raise RuntimeError('Expected exactly two verifier containers')
            info = json.loads(subprocess.check_output(self._engine_cmd('inspect', *containers), text=True))
            network_ids, projects = set(), set()
            for container in info:
                networks = container['NetworkSettings']['Networks']
                if len(networks) != 1:
                    raise RuntimeError('Verifier has an unexpected additional network')
                network_ids.add(next(iter(networks.values()))['NetworkID'])
                projects.add(container['Config']['Labels']['com.docker.compose.project'])
            if len(network_ids) != 1 or len(projects) != 1:
                raise RuntimeError('Verifier and database must share only their own network')
            network = json.loads(subprocess.check_output(
                self._engine_cmd('network', 'inspect', next(iter(network_ids))), text=True))[0]
            if (not network['Internal'] or len(network['Containers']) != 2 or
                    network.get('Labels', {}).get('com.docker.compose.project') not in projects):
                raise RuntimeError('Verifier network is not private to this trial')
            check = await self.exec(command="python -c \"import socket; s=socket.socket(); s.settimeout(2); assert s.connect_ex(('1.1.1.1',443)) != 0; socket.create_connection(('postgres',5432),2).close()\"")
            if check.return_code != 0:
                raise RuntimeError('Verifier database connectivity or Internet isolation failed')

    async def _local_service_hosts(self):
        """Read exact addresses on this trial's private task network."""
        services = ['user', 'workdb'] if self._native_compose_kind == 'interact' else ['postgres']
        names = [*services, self._EGRESS_CONTROL_SERVICE_NAME]
        result = await self._run_docker_compose_command(['ps', '-q', *names])
        ids = (result.stdout or '').split()
        if len(ids) != len(names):
            raise RuntimeError('Expected task services and Harbor egress sidecar')
        raw = await asyncio.to_thread(subprocess.check_output,
            self._engine_cmd('inspect', *ids), text=True, timeout=10)
        info = json.loads(raw)
        containers = {item['Config']['Labels']['com.docker.compose.service']: item for item in info}
        projects = {item['Config']['Labels']['com.docker.compose.project'] for item in info}
        if set(containers) != set(names) or len(projects) != 1:
            raise RuntimeError('Task services must belong to one Compose project')
        expected = 'task' if self._native_compose_kind == 'interact' else 'default'
        shared = None
        for network in containers[self._EGRESS_CONTROL_SERVICE_NAME]['NetworkSettings']['Networks'].values():
            raw = await asyncio.to_thread(subprocess.check_output,
                self._engine_cmd('network', 'inspect', network['NetworkID']), text=True, timeout=10)
            detail = json.loads(raw)[0]
            labels = detail.get('Labels', {})
            if detail['Internal'] and labels.get('com.docker.compose.network') == expected:
                if labels.get('com.docker.compose.project') not in projects or shared is not None:
                    raise RuntimeError('Task network must be private to this Compose project')
                shared = network['NetworkID']
        if shared is None:
            raise RuntimeError('Harbor egress sidecar has no private task network')
        hosts = []
        for service in services:
            matches = [value for value in containers[service]['NetworkSettings']['Networks'].values()
                       if value['NetworkID'] == shared]
            if len(matches) != 1 or not matches[0].get('IPAddress'):
                raise RuntimeError('Task service has no address on the private network: ' + service)
            for key in ('IPAddress', 'GlobalIPv6Address'):
                if value := matches[0].get(key):
                    hosts.append(str(ip_address(value)))
        return hosts

    async def _apply_network_policy(self, network_policy):
        if (self._enable_egress_control and self._native_compose_kind
                and network_policy.network_mode != NetworkMode.PUBLIC):
            hosts = await self._local_service_hosts()
            network_policy = NetworkPolicy(network_mode=NetworkMode.ALLOWLIST,
                allowed_hosts=list(dict.fromkeys([*network_policy.allowed_hosts, *hosts])))
        await super()._apply_network_policy(network_policy)

    async def stop(self, delete):
        try:
            await super().stop(delete)
        finally:
            if self._runtime_compose_dir is not None:
                self._runtime_compose_dir.cleanup()
                self._runtime_compose_dir = self._runtime_compose_path = None

    async def _check_interact_network(self):
        result = await self._run_docker_compose_command(['ps', '-q'])
        containers = (result.stdout or '').split()
        if len(containers) != 3:
            raise RuntimeError('Expected Interact main, user and workdb services')
        info = json.loads(subprocess.check_output(self._engine_cmd('inspect', *containers), text=True))
        private_ids = set()
        for container in info:
            service = container['Config']['Labels']['com.docker.compose.service']
            networks = container['NetworkSettings']['Networks']
            private = []
            for network in networks.values():
                detail = json.loads(subprocess.check_output(
                    self._engine_cmd('network', 'inspect', network['NetworkID']), text=True))[0]
                if detail['Internal']:
                    private.append(network['NetworkID'])
            if len(private) != 1 or (service != 'user' and len(networks) != 1):
                raise RuntimeError('Interact solver and workdb must use only their private network')
            private_ids.update(private)
        if len(private_ids) != 1:
            raise RuntimeError('Interact services do not share one private task network')
        check = await self.exec(command="python -c \"import socket; s=socket.socket(); s.settimeout(2); assert s.connect_ex(('1.1.1.1',443)) != 0; socket.create_connection(('user',8000),2).close(); socket.create_connection(('workdb',5432),2).close()\"")
        if check.return_code != 0:
            raise RuntimeError('Interact task services or solver Internet isolation failed')

    async def _wait_for_critic15_frappe_entrypoint(self, timeout_sec=120):
        # Compose reports a container without a healthcheck as ready before its
        # entrypoint finishes. The original Frappe verifier also starts MariaDB;
        # running it during initial recovery can launch a second daemon whose
        # mariadbd-safe then kills the first one. Wait without changing services.
        markers = (self.environment_dir / 'critic15.json',
                   self.environment_dir.parent / 'tests' / 'critic15.json')
        needs_wait = False
        for marker in markers:
            if marker.is_file():
                try:
                    metadata = json.loads(marker.read_text())
                except (OSError, ValueError) as exc:
                    raise RuntimeError('Cannot read BIRD-Critic 1.5 startup metadata') from exc
                if not isinstance(metadata, dict):
                    raise RuntimeError('Invalid BIRD-Critic 1.5 startup metadata')
                needs_wait |= metadata.get('repo_dir') == '/home/frappe/frappe-bench/apps'
        if not needs_wait:
            return

        try:
            async with asyncio.timeout(timeout_sec):
                result = await self._run_docker_compose_command(
                    ['ps', '-q', 'main'], timeout_sec=10)
                containers = (result.stdout or '').split()
                if result.return_code != 0 or len(containers) != 1:
                    raise RuntimeError('Expected one BIRD-Critic 1.5 Frappe container')
                raw = await asyncio.to_thread(
                    subprocess.check_output,
                    self._engine_cmd('inspect', containers[0]), text=True, timeout=10)
                info = json.loads(raw)
                if (len(info) != 1 or
                        info[0].get('Config', {}).get('Entrypoint') != ['/start-services.sh'] or
                        info[0].get('Config', {}).get('Cmd') != ['sh', '-c', 'sleep infinity'] or
                        info[0].get('State', {}).get('Running') is not True):
                    raise RuntimeError('Unexpected BIRD-Critic 1.5 Frappe startup configuration')
                while True:
                    result = await self._run_docker_compose_command(
                        ['exec', '-T', 'main', 'cat', '/proc/1/cmdline'], timeout_sec=10)
                    command = result.stdout or ''
                    if result.return_code != 0 or not command.endswith('\0'):
                        raise RuntimeError('Cannot read BIRD-Critic 1.5 Frappe entrypoint state')
                    if command in ('sh\0-c\0sleep infinity\0', 'sleep\0infinity\0'):
                        return
                    await asyncio.sleep(0.5)
        except TimeoutError as exc:
            raise RuntimeError(
                f'BIRD-Critic 1.5 Frappe entrypoint did not finish within {timeout_sec}s'
            ) from exc
