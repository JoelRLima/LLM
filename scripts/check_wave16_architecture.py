"""Static ownership and mutation gate for model compatibility."""

from __future__ import annotations

import argparse
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_MUTATION_ARMS = tuple(f"W16-M{index:02d}" for index in range(1, 17))
SEMANTIC_ROOTS = ("agent/planning", "agent/orchestration", "agent/tools", "agent/memory", "agent/interaction", "agent/runtime")
FORBIDDEN_IDENTITY_TOKENS = ("qwen", "llama", "gemma", "mistral", "deepseek")
LOCAL_COPY_EXCLUDES = (
    ".git",
    ".venv",
    ".audit-local",
    ".agent-local",
    "reports",
    "__pycache__",
    ".pytest_cache",
    ".pytest_temp",
    ".tmp",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".hypothesis",
    "htmlcov",
    "build",
    "dist",
    "*.egg-info",
)


@dataclass(frozen=True, slots=True)
class ArchitectureViolation:
    rule_id: str
    path: str
    detail: str

    def format(self) -> str:
        return f"{self.rule_id} {self.path}: {self.detail}"


@dataclass(frozen=True, slots=True)
class MutationArm:
    arm_id: str
    description: str
    mutate: Callable[[Path], bool]


def _source(root: Path, relative: str) -> str:
    try:
        return (root / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def _violation(rule: str, path: str, detail: str) -> ArchitectureViolation:
    return ArchitectureViolation(rule, path, detail)


def _check_required_owners(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    compatibility = _source(root, "agent/llm/model_compatibility.py")
    geometry = _source(root, "agent/llm/request_geometry.py")
    if compatibility.count("class ModelCompatibility") != 1:
        findings.append(_violation("W16-C01", "agent/llm/model_compatibility.py", "one typed compatibility owner is required"))
    if geometry.count("class EffectiveRequestGeometry") != 1:
        findings.append(_violation("W16-C02", "agent/llm/request_geometry.py", "one request-geometry owner is required"))
    return findings


def _check_profile_and_geometry(root: Path) -> list[ArchitectureViolation]:
    profile = _source(root, "agent/llm/model_profile.py")
    geometry = _source(root, "agent/llm/request_geometry.py")
    findings: list[ArchitectureViolation] = []
    required_profile = ("compatibility: ModelCompatibility",)
    if not all(item in profile for item in required_profile) or profile.count('"compatibility": self.compatibility.to_dict(),') != 2:
        findings.append(_violation("W16-C03", "agent/llm/model_profile.py", "compatibility is not typed and fingerprinted/serialized"))
    if (
        "StructuredOutputMode.GBNF" not in geometry
        or "StructuredOutputMode.JSON_SCHEMA" not in geometry
        or "STRUCTURED_REASONING_DISABLED_BY_PROFILE" not in geometry
    ):
        findings.append(_violation("W16-C04", "agent/llm/request_geometry.py", "required constrained modes/reason code are missing"))
    constrained_start = geometry.find("_CONSTRAINED_MODES")
    constrained_slice = (
        geometry[constrained_start : constrained_start + 220]
        if constrained_start >= 0
        else ""
    )
    if "StructuredOutputMode.JSON_PROMPT" in constrained_slice:
        findings.append(_violation("W16-C05", "agent/llm/request_geometry.py", "JSON_PROMPT must not be treated as constrained"))
    return findings


def _check_consumers(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for rule, path in (
        ("W16-C06", "agent/llm/session_requests.py"),
        ("W16-C07", "agent/interaction/resolver.py"),
        ("W16-C08", "agent/interaction/response.py"),
    ):
        source = _source(root, path)
        if "resolve_effective_request_geometry" not in source:
            findings.append(_violation(rule, path, "productive builder bypasses the canonical geometry owner"))
        prompt_index = source.rfind("build_effective_system_prompt_for_budget(")
        prompt_slice = source[prompt_index : prompt_index + 300] if prompt_index >= 0 else ""
        if "geometry.effective_reasoning_budget" not in prompt_slice:
            findings.append(_violation(rule, path, "prompt/transport projection does not use effective geometry"))
    return findings


def _check_provider_and_observability(root: Path) -> list[ArchitectureViolation]:
    provider = _source(root, "agent/llm/providers/openai_compatible.py")
    contracts = _source(root, "agent/llm/contracts.py")
    metrics = _source(root, "agent/llm/model_metrics.py")
    findings: list[ArchitectureViolation] = []
    if "tool_calls: bool = False" not in contracts:
        findings.append(_violation("W16-C09", "agent/llm/contracts.py", "native tool calls must remain disabled by default"))
    if any(token in provider.casefold() for token in FORBIDDEN_IDENTITY_TOKENS) or "compatibility_reason_code" in provider:
        findings.append(_violation("W16-C10", "agent/llm/providers/openai_compatible.py", "provider edge must not infer compatibility from identity"))
    if "requested_reasoning_budget" not in metrics or "effective_reasoning_budget" not in metrics or "finish_reason" not in metrics:
        findings.append(_violation("W16-C11", "agent/llm/model_metrics.py", "bounded geometry observability is incomplete"))
    if (
        "reasoning_text" in metrics
        or "response.reasoning" in metrics
        or 'getattr(response, "reasoning"' in metrics
    ):
        findings.append(_violation("W16-C12", "agent/llm/model_metrics.py", "reasoning content must not enter metrics"))
    return findings


def _check_portability_and_canaries(root: Path) -> list[ArchitectureViolation]:
    runner = _source(root, "scripts/run_evaluation_campaign.py")
    canary = _source(root, "agent/evaluation/model_compatibility_canaries.py")
    canary_cli = _source(root, "scripts/run_model_compatibility_canaries.py")
    findings: list[ArchitectureViolation] = []
    if "OpenAICompatibleGateway" in runner:
        findings.append(_violation("W16-C13", "scripts/run_evaluation_campaign.py", "evaluation must use the canonical provider factory"))
    if "--live-model-authorized" not in runner or "arguments.live_model_authorized" not in runner or "arguments.qwen_loaded" in runner:
        findings.append(_violation("W16-C14", "scripts/run_evaluation_campaign.py", "live authorization must be generic internally"))
    if any(token in canary.casefold() for token in ("release_ready", "not_release_ready", "release_verdict")):
        findings.append(_violation("W16-C15", "agent/evaluation/model_compatibility_canaries.py", "canaries must be diagnostic only"))
    if 'default="deterministic"' not in canary_cli or "DeterministicCanaryGateway" not in canary_cli:
        findings.append(_violation("W16-C16", "scripts/run_model_compatibility_canaries.py", "deterministic canary mode must be the zero-network default"))
    return findings


def _check_model_agnostic_semantics(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative_root in SEMANTIC_ROOTS:
        base = root / relative_root
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            try:
                source = path.read_text(encoding="utf-8").casefold()
            except (OSError, UnicodeError):
                continue
            for token in FORBIDDEN_IDENTITY_TOKENS:
                if token in source:
                    findings.append(_violation("W16-C17", path.relative_to(root).as_posix(), f"semantic owner contains model identity token {token!r}"))
    return findings


def _check_config_schema(root: Path) -> list[ArchitectureViolation]:
    source = _source(root, "agent/runtime/config_schema.py")
    findings: list[ArchitectureViolation] = []
    if '"compatibility"' not in source or "_validate_compatibility" not in source:
        findings.append(_violation("W16-C18", "agent/runtime/config_schema.py", "authorized strict compatibility ingress is missing"))
    return findings


def check_architecture(root: Path = ROOT) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    findings.extend(_check_required_owners(root))
    findings.extend(_check_profile_and_geometry(root))
    findings.extend(_check_consumers(root))
    findings.extend(_check_provider_and_observability(root))
    findings.extend(_check_portability_and_canaries(root))
    findings.extend(_check_model_agnostic_semantics(root))
    findings.extend(_check_config_schema(root))
    return findings


def _replace_once(path: Path, old: str, new: str) -> bool:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    if old not in source:
        return False
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return True


def _replace_all(path: Path, old: str, new: str) -> bool:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    if old not in source:
        return False
    path.write_text(source.replace(old, new), encoding="utf-8")
    return True


def _append_text(path: Path, text: str) -> bool:
    try:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(text)
    except OSError:
        return False
    return True


def _mutation_arms() -> tuple[MutationArm, ...]:
    return (
        MutationArm("W16-M01", "inject model-name semantic branch", lambda root: _append_text(root / "agent/interaction/resolver.py", '\nif "qwen" in model: pass\n')),
        MutationArm("W16-M02", "remove compatibility from fingerprint", lambda root: _replace_all(root / "agent/llm/model_profile.py", '"compatibility": self.compatibility.to_dict(),', "")),
        MutationArm("W16-M03", "resolver bypasses central geometry", lambda root: _replace_all(root / "agent/interaction/resolver.py", "resolve_effective_request_geometry", "resolve_ordinary_reasoning_budget")),
        MutationArm("W16-M04", "session builder bypasses central geometry", lambda root: _replace_all(root / "agent/llm/session_requests.py", "resolve_effective_request_geometry", "resolve_ordinary_reasoning_budget")),
        MutationArm("W16-M05", "response builder bypasses central geometry", lambda root: _replace_all(root / "agent/interaction/response.py", "resolve_effective_request_geometry", "resolve_ordinary_reasoning_budget")),
        MutationArm("W16-M06", "JSON_PROMPT becomes constrained", lambda root: _replace_once(root / "agent/llm/request_geometry.py", "{StructuredOutputMode.GBNF, StructuredOutputMode.JSON_SCHEMA}", "{StructuredOutputMode.GBNF, StructuredOutputMode.JSON_SCHEMA, StructuredOutputMode.JSON_PROMPT}")),
        MutationArm("W16-M07", "GBNF compatibility adjustment omitted", lambda root: _replace_once(root / "agent/llm/request_geometry.py", "{StructuredOutputMode.GBNF, StructuredOutputMode.JSON_SCHEMA}", "{StructuredOutputMode.JSON_SCHEMA}")),
        MutationArm("W16-M08", "JSON_SCHEMA compatibility adjustment omitted", lambda root: _replace_once(root / "agent/llm/request_geometry.py", "{StructuredOutputMode.GBNF, StructuredOutputMode.JSON_SCHEMA}", "{StructuredOutputMode.GBNF}")),
        MutationArm("W16-M09", "prompt uses requested budget", lambda root: _replace_once(root / "agent/interaction/response.py", "build_effective_system_prompt_for_budget(\n        base_system,\n        geometry.effective_reasoning_budget,", "build_effective_system_prompt_for_budget(\n        base_system,\n        geometry.requested_reasoning_budget,")),
        MutationArm("W16-M10", "provider infers compatibility by model name", lambda root: _append_text(root / "agent/llm/providers/openai_compatible.py", '\nif "qwen" in request.model: pass\n')),
        MutationArm("W16-M11", "native tool calls become default", lambda root: _replace_once(root / "agent/llm/contracts.py", "tool_calls: bool = False", "tool_calls: bool = True")),
        MutationArm("W16-M12", "evaluation directly constructs provider", lambda root: _append_text(root / "scripts/run_evaluation_campaign.py", "\nOpenAICompatibleGateway(profile)\n")),
        MutationArm("W16-M13", "authorization becomes Qwen-only", lambda root: _replace_all(root / "scripts/run_evaluation_campaign.py", "arguments.live_model_authorized", "arguments.qwen_loaded")),
        MutationArm("W16-M14", "canary emits release verdict", lambda root: _append_text(root / "agent/evaluation/model_compatibility_canaries.py", '\nrelease_verdict = "RELEASE_READY"\n')),
        MutationArm("W16-M15", "reasoning content enters metric", lambda root: _append_text(root / "agent/llm/model_metrics.py", '\nreasoning_text = getattr(response, "reasoning", "")\n')),
        MutationArm("W16-M16", "deterministic gate invokes live mode", lambda root: _replace_once(root / "scripts/run_model_compatibility_canaries.py", 'default="deterministic"', 'default="live-model"')),
    )


def check_mutations(root: Path = ROOT) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for arm in _mutation_arms():
        with tempfile.TemporaryDirectory(prefix=f"w16-{arm.arm_id.lower()}-") as raw:
            copy = Path(raw) / "repo"
            shutil.copytree(
                root,
                copy,
                ignore=shutil.ignore_patterns(*LOCAL_COPY_EXCLUDES),
            )
            if not arm.mutate(copy):
                findings.append(_violation(arm.arm_id, "<mutation>", "mutation could not be applied"))
                continue
            if not check_architecture(copy):
                findings.append(_violation(arm.arm_id, "<mutation>", "checker did not reject the mutation"))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Model compatibility architecture checker")
    parser.add_argument("--root", type=Path, default=ROOT)
    arguments = parser.parse_args()
    findings = check_architecture(arguments.root)
    findings.extend(check_mutations(arguments.root))
    if findings:
        print("W16 architecture checker: FAIL")
        for finding in findings:
            print(finding.format())
        return 1
    print("W16 architecture checker: PASS")
    print("W16-M01..W16-M16: 16/16")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
