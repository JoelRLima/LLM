"""Finite availability projection: no timers, retries or generation probes."""

import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Callable, Mapping, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from llm_agent.agent.llm.errors import ModelConnectionError, ModelTimeoutError
from llm_agent.agent.llm.model_profile import ResolvedModelProfile
from llm_agent.agent.runtime.config_errors import ConfigError
from llm_agent.agent.runtime.config_repository import ConfigRepository
from llm_agent.application.task_execution import TaskExecutionStart


class ResourceState(str, Enum):
    AVAILABLE = "available"
    BUSY = "busy"
    NOT_CONFIGURED = "not_configured"
    NOT_IMPLEMENTED = "not_implemented"
    FORBIDDEN = "forbidden"
    TEMPORARY = "temporarily_unavailable"
    UNAVAILABLE = "unavailable"
    UNVERIFIED = "unverified"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class BackendAvailability:
    state: ResourceState = ResourceState.NOT_CONFIGURED
    configured: bool = False
    connectivity: str = "unverified"
    model_readiness: str = "unverified"
    reason: str = "CONFIGURATION_MISSING"
    identity: str = ""
    generation: int = 0


class Availability:
    def __init__(self) -> None:
        self.value = BackendAvailability()
        self.profile: ResolvedModelProfile | None = None

    def refresh(self, start: TaskExecutionStart) -> BackendAvailability:
        try:
            profile = (
                ConfigRepository(start.app_paths, config_path=start.config_path)
                .load(overrides={"default_model_profile": start.profile} if start.profile else None)
                .model_profile
            )
            identity = profile.fingerprint
            if not profile.api_url or not profile.model:
                raise ValueError("endpoint/model missing")
        except (ConfigError, ValueError, KeyError, TypeError, OSError):
            self.profile = None
            if self.value.configured:
                self.value = BackendAvailability(generation=self.value.generation + 1)
            return self.value
        if identity != self.value.identity or self.profile is None:
            self.profile = profile
            self.value = BackendAvailability(
                ResourceState.UNVERIFIED,
                True,
                identity=identity,
                reason="NOT_TESTED",
                generation=self.value.generation + 1,
            )
        return self.value

    def project(self, busy: bool = False) -> dict[str, Any]:
        return {
            "backend": asdict(self.value),
            "resources": {
                "files": ResourceState.BUSY if busy else ResourceState.AVAILABLE,
                "tasks": ResourceState.BUSY if busy else ResourceState.AVAILABLE,
                "tools": ResourceState.BUSY
                if busy
                else ResourceState.AVAILABLE
                if self.value.configured
                else ResourceState.NOT_CONFIGURED,
                "model": ResourceState.BUSY if busy else self.value.state,
                "approval": ResourceState.AVAILABLE,
                "attachments": ResourceState.NOT_IMPLEMENTED,
            },
        }

    def observe(self, observation: Mapping[str, Any]) -> None:
        # Only this typed, causal projection changes backend state. Generic
        # resolver/response reason codes are not evidence of transport failure.
        code = observation.get("model_transport_failure")
        if code == "MODEL_CONNECTION_ERROR":
            self.value = BackendAvailability(
                ResourceState.UNAVAILABLE, self.value.configured, "failed", "unverified",
                code, self.value.identity, self.value.generation
            )
        elif code == "MODEL_TIMEOUT":
            self.value = BackendAvailability(
                ResourceState.TEMPORARY, self.value.configured, "failed", "unverified",
                code, self.value.identity, self.value.generation
            )

    def failure(self, error: BaseException) -> None:
        # Only normalized model transport failures change backend availability.
        if isinstance(error, ModelTimeoutError):
            state, reason = ResourceState.TEMPORARY, "MODEL_TIMEOUT"
        elif isinstance(error, ModelConnectionError):
            state, reason = ResourceState.UNAVAILABLE, "MODEL_CONNECTION_ERROR"
        else:
            return
        self.value = BackendAvailability(
            state, self.value.configured, "failed", "unverified", reason, self.value.identity, self.value.generation
        )

    def test(self, probe: Callable[[ResolvedModelProfile], str] | None = None) -> dict[str, Any]:
        if self.profile is None:
            return self.project()
        generation = self.value.generation
        transport_lock = (
            self.value
            if self.value.state in {ResourceState.UNAVAILABLE, ResourceState.TEMPORARY}
            else None
        )
        try:
            ready = (probe or probe_catalog)(self.profile)
            value = BackendAvailability(
                ResourceState.AVAILABLE, True, "verified", ready, "CATALOG_REACHABLE", self.value.identity, generation
            )
        except (
            PermissionError,
            TimeoutError,
            ModelTimeoutError,
            ConnectionError,
            URLError,
            ModelConnectionError,
            ValueError,
            OSError,
            LookupError,
            json.JSONDecodeError,
        ):
            # Failures of the metadata endpoint are inconclusive for inference.
            # Preserve a prior execution failure; only positive test clears it.
            value = transport_lock or BackendAvailability(
                ResourceState.UNVERIFIED,
                True,
                "unverified",
                "unverified",
                "CATALOG_INCONCLUSIVE",
                self.value.identity,
                generation,
            )
        if self.value.generation == generation:
            self.value = value
        return self.project()


def _catalog_url(profile: ResolvedModelProfile) -> str:
    """One bounded metadata GET. Never invoke completions or load a model."""
    parts = urlsplit(profile.api_url)
    if parts.scheme not in ("http", "https") or parts.username or parts.password:
        raise ValueError("unsupported endpoint")
    path = parts.path.rstrip("/")
    if path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")]
    elif path.endswith("/completions"):
        path = path[: -len("/completions")]
    return urlunsplit((parts.scheme, parts.netloc, path + "/models", "", ""))


def _catalog_models(data: bytes) -> list[dict[str, Any]]:
    catalog = json.loads(data)
    if not isinstance(catalog, dict) or not isinstance(catalog.get("data"), list):
        raise ValueError("invalid catalog")
    models = catalog.get("data", [])
    if any(not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] for item in models):
        raise ValueError("invalid catalog model entry")
    return cast(list[dict[str, Any]], models)


def probe_catalog(profile: ResolvedModelProfile) -> str:
    """One bounded metadata GET. Never invoke completions or load a model."""
    url = _catalog_url(profile)
    try:
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=min(5, profile.timeout)) as response:
            data = response.read(262145)
            if len(data) > 262144:
                raise ValueError("catalog too large")
    except HTTPError as error:
        if error.code in (401, 403):
            raise PermissionError() from error
        # A 404 can come from the route, proxy or provider policy; it does not
        # establish that model generation is unavailable. 405/501 do establish
        # that this metadata method is unsupported, not that inference is down.
        if error.code in (405, 501):
            raise LookupError("catalog GET is not implemented") from error
        raise ValueError(f"catalog inconclusive (HTTP {error.code})") from error
    except URLError as error:
        if isinstance(error.reason, TimeoutError):
            raise TimeoutError() from error
        raise
    models = _catalog_models(data)
    return (
        "listed" if any(item.get("id") == profile.model for item in models if isinstance(item, dict)) else "unverified"
    )
