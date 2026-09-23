"""CLI-owned projection into the neutral Discovery core."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, cast

from agent.actions.defaults import DEFAULT_ACTION_CATALOG
from agent.discovery.contracts import (
    DISCOVERY_REQUIRES_ATTENTION,
    DISCOVERY_REQUIRES_IDLE,
    DiscoveryAvailability,
    DiscoveryControllerState,
    DiscoveryEntryV1,
    DiscoveryExecutionContext,
    DiscoveryResultV1,
)
from agent.discovery.index import DiscoveryCatalog, action_entries, cli_entries_from_parser
from agent.discovery.semantic import SemanticCommandDiscovery
from agent.discovery.service import DiscoveryService, mcp_engineering_availability
from agent.discovery.store import FrecencyStore
from agent.interfaces.cli.action_registry import DEFAULT_CLI_ACTION_REGISTRY, CliActionBinding
from agent.interfaces.cli.parser import build_parser


def controller_state(value: object | None) -> DiscoveryControllerState | None:
    raw = getattr(value, "state", value)
    raw = getattr(raw, "value", raw)
    if raw is None:
        return None
    try:
        return DiscoveryControllerState(str(raw))
    except ValueError:
        return None


def availability_for_binding(
    binding: CliActionBinding,
    state: DiscoveryControllerState | None,
) -> DiscoveryAvailability:
    if state is None:
        # A non-chat/static projection has no controller state to evaluate;
        # keep every action discoverable and let canonical admission decide.
        return DiscoveryAvailability(True)
    if binding.busy_policy == "ALWAYS_LOCAL":
        return DiscoveryAvailability(True)
    if binding.busy_policy == "IDLE_ONLY":
        return DiscoveryAvailability(state is DiscoveryControllerState.IDLE, None if state is DiscoveryControllerState.IDLE else DISCOVERY_REQUIRES_IDLE)
    if binding.busy_policy == "ATTENTION_ONLY":
        return DiscoveryAvailability(state is DiscoveryControllerState.WAITING_ATTENTION, None if state is DiscoveryControllerState.WAITING_ATTENTION else DISCOVERY_REQUIRES_ATTENTION)
    if binding.busy_policy == "AGENTIC_SUBMIT":
        return DiscoveryAvailability(True)
    return DiscoveryAvailability(False, "DISCOVERY_AVAILABILITY_UNKNOWN")


def build_catalog(
    *,
    engineering_views: Iterable[object] = (),
    workspace_bound: bool = False,
) -> tuple[DiscoveryCatalog, Mapping[str, DiscoveryAvailability]]:
    parser_entries = cli_entries_from_parser(build_parser())
    entries: list[DiscoveryEntryV1] = [
        *action_entries(DEFAULT_ACTION_CATALOG, bindings=DEFAULT_CLI_ACTION_REGISTRY._bindings),
        *parser_entries,
    ]
    availability: dict[str, DiscoveryAvailability] = {
        f"action:{binding.action_id}": availability_for_binding(binding, None)
        for binding in DEFAULT_CLI_ACTION_REGISTRY._bindings
    }
    for entry in parser_entries:
        # The command's own argparse help is the authority for this optional
        # surface; Discovery does not maintain a second MCP command registry.
        is_mcp_surface = "mcp" in entry.description.casefold() and "stdio" in entry.description.casefold()
        if is_mcp_surface:
            mcp_availability = mcp_engineering_availability()
            availability[entry.entry_id] = (
                mcp_availability
                if not mcp_availability.available or workspace_bound
                else DiscoveryAvailability(False, "ENGINEERING_WORKSPACE_REQUIRED")
            )
        else:
            availability[entry.entry_id] = DiscoveryAvailability(True)
    if engineering_views:
        from agent.discovery.index import engineering_entries

        engineering, engineering_availability = engineering_entries(engineering_views)
        entries.extend(engineering)
        availability.update(engineering_availability)
    return DiscoveryCatalog(entries), availability


def build_service(
    *,
    app_paths: object | None = None,
    controller: object | None = None,
    workspace_bound: bool = False,
    engineering_views: Iterable[object] = (),
    semantic: bool = False,
    gateway_config: Any | None = None,
    config_path: str | Path | None = None,
    profile: str | None = None,
    home: str | Path | None = None,
) -> tuple[DiscoveryService, DiscoveryExecutionContext]:
    catalog, availability = build_catalog(
        engineering_views=engineering_views,
        workspace_bound=workspace_bound,
    )
    state = controller_state(controller)
    projected = dict(availability)
    for binding in DEFAULT_CLI_ACTION_REGISTRY._bindings:
        projected[f"action:{binding.action_id}"] = availability_for_binding(binding, state)
    context = DiscoveryExecutionContext(
        controller_state=state,
        workspace_bound=workspace_bound,
        availability_by_entry_id=projected,
    )
    semantic_config = gateway_config
    if semantic and semantic_config is None:
        semantic_config = _resolve_semantic_gateway_config(
            app_paths=app_paths,
            config_path=config_path,
            profile=profile,
            home=home,
        )
    semantic_owner = (
        SemanticCommandDiscovery(gateway_config=semantic_config)
        if semantic and semantic_config is not None
        else None
    )
    service = DiscoveryService(
        catalog,
        frecency=FrecencyStore.for_app_paths(app_paths) if app_paths is not None else FrecencyStore(),
        semantic=semantic_owner,
    )
    return service, context


def discover_commands(
    query: str = "",
    *,
    app_paths: object | None = None,
    controller: object | None = None,
    workspace_bound: bool = False,
    engineering_views: Iterable[object] = (),
    semantic: bool = False,
    semantic_allowed: bool = False,
    gateway_config: Any | None = None,
    config_path: str | Path | None = None,
    profile: str | None = None,
    home: str | Path | None = None,
) -> DiscoveryResultV1:
    service, context = build_service(
        app_paths=app_paths,
        controller=controller,
        workspace_bound=workspace_bound,
        engineering_views=engineering_views,
        semantic=semantic,
        gateway_config=gateway_config,
        config_path=config_path,
        profile=profile,
        home=home,
    )
    return service.search(
        query,
        context=context,
        semantic_requested=semantic,
        semantic_allowed=semantic_allowed,
    )


def record_discovery_use(entry_id: str, app_paths: object | None) -> None:
    """Persist a real palette selection through the canonical Discovery owner."""
    if app_paths is not None:
        service, _ = build_service(app_paths=app_paths)
        service.record_use(entry_id)


def _resolve_semantic_gateway_config(
    *,
    app_paths: object | None,
    config_path: str | Path | None,
    profile: str | None,
    home: str | Path | None,
) -> Any | None:
    """Resolve the selected model profile without starting the application.

    Local Discovery never needs configuration. An explicit semantic request
    resolves the same ConfigRepository/profile seam used by application
    startup, but does not initialize storage, mutate the profile, or open the
    full application runtime.
    """

    try:
        from agent.runtime.config_repository import ConfigRepository
        from agent.runtime.paths import AppPaths

        paths = cast(AppPaths, app_paths) if app_paths is not None else AppPaths.discover(app_home=home)
        repository = ConfigRepository(paths, config_path=config_path)
        overrides = {"default_model_profile": profile} if profile is not None else None
        return repository.load(overrides=overrides).model_profile
    except Exception:
        # ConfigError/ConfigNotFound and schema/provider-shape failures are
        # semantic prerequisites, never reasons to trigger first-run setup.
        return None


__all__ = [
    "availability_for_binding",
    "build_catalog",
    "build_service",
    "controller_state",
    "discover_commands",
    "record_discovery_use",
]
