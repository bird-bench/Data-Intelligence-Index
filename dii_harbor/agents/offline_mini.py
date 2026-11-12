"""Run mini-swe on the host and execute its commands through Harbor."""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
from importlib.metadata import version
import json
import threading

import yaml

from harbor.agents.installed.mini_swe_agent import MiniSweAgent
from minisweagent.agents.default import DefaultAgent
from minisweagent.config import get_config_from_spec
from minisweagent.environments.local import LocalEnvironment, LocalEnvironmentConfig
from minisweagent.models import get_model
from minisweagent.utils.serialize import recursive_merge


class _Stopped(BaseException):
    """Stop the worker without treating cancellation as a tool error."""


class _HarborEnvironment:
    def __init__(self, environment, loop, config, stopped, uname):
        self.environment = environment
        self.loop = loop
        self.config = LocalEnvironmentConfig(**config)
        self.stopped = stopped
        self.uname = uname
        self.pending: Future | None = None
        self.lock = threading.Lock()

    def execute(self, action, cwd="", *, timeout=None):
        with self.lock:
            if self.stopped.is_set():
                raise _Stopped()
            self.pending = asyncio.run_coroutine_threadsafe(
                self.environment.exec(
                    command=action.get("command", ""),
                    cwd=cwd or self.config.cwd or None,
                    env=self.config.env,
                    timeout_sec=timeout or self.config.timeout,
                ),
                self.loop,
            )
            pending = self.pending
        try:
            result = pending.result()
            output = {
                "output": (result.stdout or "") + (result.stderr or ""),
                "returncode": result.return_code,
                "exception_info": "",
            }
        except Exception as exc:
            if self.stopped.is_set():
                raise _Stopped() from None
            output = {
                "output": "",
                "returncode": -1,
                "exception_info": f"An error occurred while executing the command: {exc}",
                "extra": {"exception_type": type(exc).__name__, "exception": str(exc)},
            }
        finally:
            with self.lock:
                self.pending = None
        if self.stopped.is_set():
            raise _Stopped()
        LocalEnvironment._check_finished(self, output)
        return output

    def stop(self):
        self.stopped.set()
        with self.lock:
            if self.pending is not None:
                self.pending.cancel()

    def get_template_vars(self, **kwargs):
        return recursive_merge(self.config.model_dump(), self.uname, kwargs)

    def serialize(self):
        return {"info": {"config": {
            "environment": self.config.model_dump(mode="json"),
            "environment_type": f"{__name__}._HarborEnvironment",
        }}}


class _HostModel:
    """Keep connection secrets outside mini-swe's saved model config."""

    def __init__(self, model, connection, stopped):
        self.model = model
        self.connection = connection
        self.stopped = stopped

    def __getattr__(self, name):
        return getattr(self.model, name)

    def query(self, messages, **kwargs):
        if self.stopped.is_set():
            raise _Stopped()
        message = self.model.query(messages, **(self.connection | kwargs))
        if self.stopped.is_set():
            raise _Stopped()
        return message


class OfflineMiniSweAgent(MiniSweAgent):
    """Use the standard mini-swe loop with an offline Harbor task environment."""

    @staticmethod
    def name():
        return "offline-mini-swe-agent"

    @property
    def extra_env(self):
        # Harbor normally exports agent env into every task command.
        # Connection variables belong to the host model process here.
        return {}

    async def setup(self, environment):
        installed = version("mini-swe-agent")
        expected = self._version or "2.4.6"
        if installed != expected:
            raise RuntimeError(f"Host mini-swe-agent must be {expected}; found {installed}")
        self._version = installed
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def _host_config(self):
        config = recursive_merge(
            get_config_from_spec("mini"),
            {"model": {"model_kwargs": {"timeout": 3600}}},
            yaml.safe_load(self._config_yaml) if self._config_yaml else {},
        )
        model = config.setdefault("model", {})
        model["model_name"] = self.model_name
        model["cost_tracking"] = "ignore_errors"
        kwargs = model.setdefault("model_kwargs", {})
        if self.model_name.startswith("openrouter/"):
            # OpenRouter chat completions (docs: reasoning tokens, prompt caching):
            # reasoning.effort maps to Anthropic's output_config.effort; top-level
            # cache_control turns on automatic prompt caching instead of mini-swe's
            # explicit breakpoints.
            model["model_class"] = "dii_harbor.agents.openrouter_model.LongThinkingOpenRouterModel"
            model["model_name"] = self.model_name.removeprefix("openrouter/")
            model["set_cache_control"] = None
            kwargs.pop("store", None)  # OpenAI Responses option from the shared mini config
            kwargs["cache_control"] = {"type": "ephemeral"}
            if self.options.reasoning_effort:
                kwargs["reasoning"] = {"effort": self.options.reasoning_effort}
        elif self.options.reasoning_effort:
            if self.model_name.startswith("openai/"):
                model["model_class"] = "litellm_response"
                kwargs["reasoning"] = {"effort": self.options.reasoning_effort}
            else:
                kwargs["reasoning_effort"] = self.options.reasoning_effort
        if self.options.max_tokens is not None:
            key = ("max_output_tokens" if self.model_name.startswith("openai/")
                   and self.options.reasoning_effort else "max_tokens")
            kwargs[key] = self.options.max_tokens
        if self.session_id:
            headers = self._session_id_headers if self._session_id_headers is not None else ["X-Session-ID"]
            kwargs.setdefault("extra_headers", {}).update({key: self.session_id for key in headers})
        agent = config.setdefault("agent", {})
        agent["cost_limit"] = float(self.options.cost_limit or 0)
        agent["output_path"] = self.logs_dir / "mini-swe-agent.trajectory.json"
        # These options only affect the interactive CLI, which runs in yolo mode.
        for key in ("agent_class", "mode", "confirm_exit", "whitelist_actions"):
            agent.pop(key, None)
        return config

    async def run(self, instruction, environment, context):
        if not self.model_name or "/" not in self.model_name:
            raise ValueError("Model name must be in the format provider/model_name")
        access = self.model_connection
        if access.api_key is None:
            raise ValueError("No API key found for the host mini-swe model")
        connection = {"api_key": access.api_key}
        if access.configured_base_url:
            connection["api_base"] = access.configured_base_url
        config = self._host_config()
        if self._litellm_model_registry_json:
            import litellm
            litellm.utils.register_model(json.loads(self._litellm_model_registry_json))
        # Read system information from the task container, not the host.
        system = await environment.exec(
            command="uname -s; uname -r; uname -v; uname -m", timeout_sec=30,
        )
        uname = dict(zip(("system", "release", "version", "machine"), system.stdout.splitlines()))
        stopped = threading.Event()
        bridge = _HarborEnvironment(environment, asyncio.get_running_loop(),
                                    config.get("environment", {}), stopped, uname)
        model = _HostModel(get_model(config=config["model"]), connection, stopped)
        agent = DefaultAgent(model, bridge, **config["agent"])
        instruction = self.render_instruction(instruction)
        if self.mcp_servers:
            instruction += "\n\nMCP Servers:\nThe following MCP servers are available for this task.\n"
            for server in self.mcp_servers:
                detail = (f"stdio transport, command: {server.command} {' '.join(server.args)}"
                          if server.transport == "stdio" else f"{server.transport} transport, url: {server.url}")
                instruction += f"- {server.name}: {detail}\n"

        def run_worker():
            try:
                return agent.run(instruction)
            except _Stopped:
                return None

        worker = asyncio.create_task(asyncio.to_thread(run_worker))
        try:
            await worker
        finally:
            bridge.stop()
            self.populate_context_post_run(context)
