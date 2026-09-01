from pathlib import Path

from scripts import check_wave7_architecture as checker


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _rules(violations: list[checker.ArchitectureViolation]) -> set[str]:
    return {item.rule_id for item in violations}


def test_current_repository_passes_w7_source_checks(monkeypatch) -> None:
    monkeypatch.setattr(checker, "_check_prior_checkers", lambda root: [])

    assert checker.check_architecture(Path(__file__).resolve().parents[3]) == []


def test_s3_rejects_retired_production_import(tmp_path: Path) -> None:
    _write(tmp_path, "agent/rogue.py", "from agent.llm.model_client import ModelClient\n")

    violations = checker._check_s3(tmp_path)

    assert "W7-S3" in _rules(violations)
    assert any("model_client" in item.detail for item in violations)


def test_s2_rejects_retired_replan_module(tmp_path: Path) -> None:
    _write(tmp_path, "agent/planning/replan_compat.py", "")

    violations = checker._check_s2(tmp_path)

    assert "W7-S2" in _rules(violations)
    assert any("replan_compat.py" in item.path for item in violations)


def test_s2_rejects_retired_plan_decoder_module(tmp_path: Path) -> None:
    _write(tmp_path, "agent/state_plan.py", "def canonicalize_plan_steps():\n    pass\n")

    violations = checker._check_s2(tmp_path)

    assert "W7-S2" in _rules(violations)
    assert any("state_plan.py" in item.path for item in violations)


def test_s4_rejects_recreated_shim_but_ignores_documentation_text(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/rogue.py",
        '"""Historical ModelClient and send_request names are documented only."""\n'
        "class LegacyModelClient:\n"
        "    def send_request(self):\n"
        "        return None\n",
    )

    violations = checker._check_s4(tmp_path)

    assert "W7-S4" in _rules(violations)
    assert any("LegacyModelClient" in item.detail for item in violations)
    assert any("send_request" in item.detail for item in violations)


def test_s4_rejects_replan_alias_exports(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/rogue.py",
        "from agent.planning.replan_models import ReplanContext\n"
        "ReplanContextCompat = ReplanContext\n"
        "RetryPolicy = object\n",
    )

    violations = checker._check_s4(tmp_path)

    assert "W7-S4" in _rules(violations)
    assert any("ReplanContextCompat" in item.detail for item in violations)
    assert any("RetryPolicy" in item.detail for item in violations)


def test_s11_rejects_task_policy_admit_reintroduction(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/runtime/task_policy.py",
        "class TaskRuntimePolicy:\n"
        "    def admit(self, requested_units=1):\n"
        "        return requested_units\n",
    )

    violations = checker._check_s11(tmp_path)

    assert "W7-S11" in _rules(violations)
    assert any("admit" in item.detail for item in violations)


def test_s11_rejects_resource_compatibility_alias_reintroduction(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/rogue.py",
        "from agent.resources.contracts import ResourceAccess\n"
        "ResourceClaim = ResourceAccess\n"
        "def normalize_resource_name(value):\n"
        "    return value\n",
    )

    violations = checker._check_s4(tmp_path)

    assert "W7-S4" in _rules(violations)
    assert any("ResourceClaim" in item.detail for item in violations)
    assert any("normalize_resource_name" in item.detail for item in violations)


def test_s11_rejects_toolresult_import_hook_reintroduction(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/contracts.py",
        "def __getattr__(name):\n"
        "    if name == 'ToolResult':\n"
        "        return object\n"
        "    raise AttributeError(name)\n",
    )

    violations = checker._check_s11(tmp_path)

    assert "W7-S11" in _rules(violations)
    assert any("ToolResult import hook" in item.detail for item in violations)


def test_s11_rejects_configuration_compatibility_methods(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/runtime/config_repository.py",
        "class ResolvedConfig:\n"
        "    def to_legacy_dict(self):\n"
        "        return {}\n"
        "class ConfigRepository:\n"
        "    def load_legacy(self):\n"
        "        return {}\n",
    )

    violations = checker._check_s11(tmp_path)

    assert "W7-S11" in _rules(violations)
    assert sum("configuration compatibility method" in item.detail for item in violations) == 2


def test_s11_rejects_health_root_config_compatibility_lookup(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/health/state_checks.py",
        "import importlib\n"
        "from agent.health.core import ensure_sys_path\n"
        "def check_config():\n"
        "    ensure_sys_path()\n"
        "    return importlib.import_module('config')\n",
    )

    violations = checker._check_health_config_retirement(tmp_path)

    assert {item.rule_id for item in violations} == {"W7-S11"}
    assert any("dynamically imports retired root config" in item.detail for item in violations)
    assert any("ensure_sys_path" in item.detail for item in violations)
    assert any("must import canonical" in item.detail for item in violations)


def test_s11_rejects_operations_checkpoint_event_fallbacks(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/orchestration/operations.py",
        "from agent.runtime.event_dispatch import append_state_event\n"
        "def _save_checkpoint(self):\n"
        "    saved = None\n"
        "    if saved is not None:\n"
        "        return True\n"
        "def _emit_checkpoint_event(self, event_type, data):\n"
        "    emit = getattr(self, '_emit', None)\n"
        "    if callable(emit): emit(event_type, data)\n"
        "def _emit(self, event_type, data=None):\n"
        "    dispatcher = None\n"
        "    if dispatcher is None: append_state_event(self.agent_state, data)\n",
    )

    violations = checker._check_operations_checkpoint_retirement(tmp_path)

    assert "W7-S11" in _rules(violations)
    assert any("append_state_event" in item.detail for item in violations)
    assert any("None" in item.detail for item in violations)
    assert any("canonical event dispatcher" in item.detail for item in violations)


def test_s4_rejects_retired_cost_and_watchdog_limit_aliases(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/rogue.py",
        "DEFAULT_MAX_TASK_STEPS = 30\n"
        "DEFAULT_MAX_TASK_TOKENS = 200000\n"
        "DEFAULT_MAX_TASK_TOOL_CALLS = 60\n"
        "DEFAULT_MAX_TASK_WALL_SECONDS = 600\n"
        "DEFAULT_MAX_REPEATED_NO_PROGRESS = 3\n"
        "DEFAULT_MAX_CONSECUTIVE_SAME_ERROR = 3\n",
    )

    violations = checker._check_s4(tmp_path)

    assert "W7-S4" in {item.rule_id for item in violations}
    assert {item.detail for item in violations} == {
        "retired facade alias recreated: DEFAULT_MAX_TASK_STEPS",
        "retired facade alias recreated: DEFAULT_MAX_TASK_TOKENS",
        "retired facade alias recreated: DEFAULT_MAX_TASK_TOOL_CALLS",
        "retired facade alias recreated: DEFAULT_MAX_TASK_WALL_SECONDS",
        "retired facade alias recreated: DEFAULT_MAX_REPEATED_NO_PROGRESS",
        "retired facade alias recreated: DEFAULT_MAX_CONSECUTIVE_SAME_ERROR",
    }


def test_s11_rejects_provider_local_profile_facade(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/llm/providers/factory.py",
        "def resolve_model_profile(config):\n"
        "    return config\n",
    )

    violations = checker._check_s11(tmp_path)

    assert "W7-S11" in _rules(violations)
    assert any("provider-local" in item.detail for item in violations)


def test_s11_rejects_plan_list_identity_reintroduction(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/planning/plan_model.py",
        "class Plan(list):\n"
        "    pass\n",
    )

    violations = checker._check_s11(tmp_path)

    assert "W7-S11" in _rules(violations)
    assert any("must not inherit from list" in item.detail for item in violations)


def test_s6_rejects_legacy_invocation_bypass(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/rogue.py",
        "def run(gateway):\n"
        "    return gateway.send_request()\n",
    )

    violations = checker._check_s6(tmp_path)

    assert "W7-S6" in _rules(violations)


def test_s7_rejects_old_w6_checkpoint_write_key(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/runtime/task_policy.py",
        "class TaskRuntimePolicy:\n"
        "    pass\n",
    )
    _write(
        tmp_path,
        "agent/runtime/task_policy_state.py",
        "class TaskPolicyState:\n"
        "    def to_checkpoint_dict(self):\n"
        "        return {'consumed_logical_steps': 1, 'active_elapsed': 2.0}\n",
    )
    _write(tmp_path, "agent/checkpoint_validation.py", "")
    _write(tmp_path, "agent/planning/tool_metadata.py", "")

    violations = checker._check_s7(tmp_path)

    assert "W7-S7" in _rules(violations)
    assert any("retired key" in item.detail for item in violations)


def test_s8_rejects_missing_productive_recovery_owner(tmp_path: Path) -> None:
    _write(tmp_path, "agent/state.py", "class AgentState: pass\n")
    _write(tmp_path, "agent/runtime/task_execution_context.py", "def build_task_execution_context(): pass\n")
    _write(tmp_path, "agent/runtime/task_policy_support.py", "def refresh_orchestrator_task_policy(): pass\n")
    _write(tmp_path, "agent/runtime/context.py", "class TaskExecutionContext: pass\n")
    _write(tmp_path, "agent/runtime/task_policy.py", "class TaskRuntimePolicy: pass\n")

    violations = checker._check_s8(tmp_path)

    assert "W7-S8" in _rules(violations)
    assert any("AgentState" in item.detail for item in violations)


def test_s9_rejects_stale_or_missing_inventory(tmp_path: Path) -> None:
    violations = checker._check_s9(tmp_path)

    assert "W7-S9" in _rules(violations)


def test_s9_requires_identifiers_in_their_disposition_sections(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "docs/legado.md",
        "STATUS: CURRENT\n"
        "## Removido\n"
        "ResourceClaim\n"
        "## Retido como contrato de persistência ou leitura limitada\n"
        "LegacyToolResult\n"
        "## Reclassificado como canônico\n"
        "ResourceAccess\n"
        "## Adiado para W8 com evidência bloqueante\n"
        "LegacyEventSinkAdapter\n",
    )

    violations = checker._check_s9(tmp_path)

    assert "W7-S9" in _rules(violations)
    assert any("agent/planning/replan_compat.py" in item.detail for item in violations)


def test_s12_rejects_unledgered_compatibility_facade(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/rogue.py",
        "def resolve_profile():\n"
        "    \"\"\"Compatibility facade for the canonical profile.\"\"\"\n"
        "    return None\n",
    )

    violations = checker._check_s12(tmp_path)

    assert "W7-S12" in _rules(violations)
    assert any("no ledger disposition" in item.detail for item in violations)


def test_s12_rejects_unledgered_backwards_compatible_alias(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/rogue.py",
        "def resolve_profile():\n"
        "    \"\"\"Backwards-compatible alias for the profile resolver.\"\"\"\n"
        "    return None\n",
    )

    violations = checker._check_s12(tmp_path)

    assert "W7-S12" in _rules(violations)
    assert any("resolve_profile" in item.detail for item in violations)


def test_s12_rejects_unledgered_compatibility_alias_and_list_inheritance(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/rogue.py",
        "Canonical = object\n"
        "LegacyShape = Canonical\n"
        "class CompatibilityShape(list):\n"
        "    pass\n",
    )

    violations = checker._check_s12(tmp_path)

    assert "W7-S12" in _rules(violations)
    assert any("LegacyShape" in item.detail for item in violations)
    assert any("CompatibilityShape" in item.detail for item in violations)


def test_s12_allows_ledgered_serialized_legacy_reader(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/runtime/events.py",
        "def from_legacy(value):\n"
        "    \"\"\"Read one supported serialized event.\"\"\"\n"
        "    return value\n",
    )

    assert checker._check_s12(tmp_path) == []


def test_s12_ignores_historical_format_discussion(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/history.py",
        '"""Historical format discussion: legacy records are not live APIs."""\n'
        "def parse_record(value):\n"
        '    """Read a historical format without exposing compatibility source names."""\n'
        "    return value\n",
    )

    assert checker._check_s12(tmp_path) == []
