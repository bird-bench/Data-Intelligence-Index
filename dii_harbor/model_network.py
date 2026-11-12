"""Read model endpoint settings from an env file without touching API keys."""
from io import StringIO
from pathlib import Path
import re
from urllib.parse import urlsplit

from dotenv.parser import parse_stream
from harbor.agents.model_connection import PROVIDERS
from harbor.models.task.config import normalize_allowed_hosts


_MINI_BASE_URLS = ("OPENAI_BASE_URL", "OPENAI_API_BASE")
_ENDPOINT_NAMES = frozenset(_MINI_BASE_URLS).union(
    name for provider in PROVIDERS.values() for name in provider.base_url_envs
)
_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _endpoint_host(value: str, *, setting: str) -> str:
    """Validate an endpoint without including its value in an error."""
    try:
        if not value or any(char.isspace() for char in value):
            raise ValueError()
        endpoint = urlsplit(value)
        if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
                or endpoint.username is not None or endpoint.password is not None
                or "?" in value or "#" in value or "*" in endpoint.hostname):
            raise ValueError()
        # Access the port property to reject invalid ports.
        _ = endpoint.port
        return normalize_allowed_hosts([endpoint.hostname])[0]
    except (ValueError, TypeError, IndexError):
        raise ValueError(
            f"Invalid {setting}: use an HTTP(S) URL without credentials, query or fragment"
        ) from None


def endpoint_overrides(env_file: str | Path) -> dict[str, str]:
    """Read supported endpoint settings without parsing credential values."""
    path = Path(env_file)
    if not path.is_file():
        return {}
    selected = []
    for line in path.read_text().splitlines():
        assignment = _ASSIGNMENT.match(line)
        if assignment and assignment.group(1) in _ENDPOINT_NAMES:
            selected.append(line)
    endpoints = {}
    for binding in parse_stream(StringIO("\n".join(selected))):
        if binding.error:
            raise ValueError("Invalid model endpoint setting in the selected env file")
        name, value = binding.key, binding.value
        if value is not None:
            if value:
                _endpoint_host(value, setting=name)
            endpoints[name] = value
    return endpoints
