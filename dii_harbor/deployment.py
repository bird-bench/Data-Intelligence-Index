"""Host-specific launch settings: Docker provider, builder and proxies."""
import os
from pathlib import Path
import platform
import re
import subprocess
from urllib.parse import urlsplit, urlunsplit


def _macos_http_proxy():
    """Read the enabled system HTTP proxy; no credentials or settings are changed."""
    try:
        proc = subprocess.run(['/usr/sbin/scutil', '--proxy'], text=True,
                              capture_output=True, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    values = dict(re.findall(r'^\s*(\w+)\s*:\s*(.*?)\s*$', proc.stdout, re.M))
    for kind in ('HTTPS', 'HTTP'):
        host, port = values.get(kind + 'Proxy', ''), values.get(kind + 'Port', '')
        if values.get(kind + 'Enable') == '1' and host and port.isdigit():
            return f'http://{host}:{port}'
    return None


def _desktop_container_proxy(proxy):
    parts = urlsplit(proxy)
    if parts.hostname in {'127.0.0.1', 'localhost', '::1'}:
        auth = parts.netloc.rpartition('@')[0] + '@' if '@' in parts.netloc else ''
        parts = parts._replace(netloc=auth + 'host.docker.internal' + (f':{parts.port}' if parts.port else ''))
    return urlunsplit(parts)


def launch_settings(root, *, provider='auto', builder=None, host_proxy=None,
                    container_proxy=None, environ=None, system=None):
    env = dict(os.environ if environ is None else environ)
    desktop = (system or platform.system()) == 'Darwin'
    if provider == 'auto':
        # The Docker-native provider keeps task-local services reachable offline.
        provider = 'local'
    if provider not in {'local', 'docker'}:
        raise ValueError('Provider must be auto, local, or docker')
    environment = ({'import_path': 'dii_harbor.docker_local:LocalDockerEnvironment'}
                   if provider == 'local' else {'type': 'docker'})
    environment['delete'] = True
    # Docker's selected builder unless one is named. Do not name a Docker context
    # (such as desktop-linux) here: Compose then fails to build.
    chosen_builder = builder
    if chosen_builder is None:
        chosen_builder = env.get('DII_BUILDX_BUILDER', env.get('BUILDX_BUILDER'))
    if chosen_builder is not None:
        if chosen_builder:
            env['BUILDX_BUILDER'] = chosen_builder
        else:
            env.pop('BUILDX_BUILDER', None)
    proxy = host_proxy if host_proxy is not None else env.get('DII_HOST_PROXY')
    if proxy is None and desktop:
        proxy = env.get('HTTPS_PROXY') or env.get('https_proxy') or _macos_http_proxy()
    if proxy is not None:
        for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy'):
            if proxy:
                env[key] = proxy
            else:
                env.pop(key, None)
        env.pop('ALL_PROXY', None)
        env.pop('all_proxy', None)
    proxy_in_container = (container_proxy if container_proxy is not None
                          else env.get('DII_CONTAINER_PROXY'))
    if proxy_in_container is None:
        proxy_in_container = _desktop_container_proxy(proxy or '') if desktop else ''
    env['DII_CONTAINER_PROXY'] = proxy_in_container
    env['PYTHONPATH'] = str(Path(root).resolve()) + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    # Addresses can contain credentials. Record presence, never their values.
    info = {'provider': provider, 'builder': env.get('BUILDX_BUILDER'),
            'host_proxy_configured': any(env.get(k) for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy')),
            'container_proxy_configured': bool(proxy_in_container)}
    return env, environment, info
