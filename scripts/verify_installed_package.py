"""Build and verify the installed wheel from outside the source checkout.

The default is the acceptance gate: it creates a venv without system packages
and asks pip to resolve every dependency declared by the wheel. Consequently,
it requires an available package index or wheelhouse.

``--offline-diagnostic`` is intentionally weaker: it reuses packages from the
base interpreter and skips dependency resolution. It can diagnose packaging and
installed behavior locally, but it never represents complete acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import venv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
DECLARED_RUNTIME_IMPORTS = ("ddgs", "requests", "rich")

INSTALLED_PROBE_SOURCE = """\
from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

from agent.skills import load_skill_registry


workspace = Path(sys.argv[1]).resolve()
sentinel = Path(sys.argv[2]).resolve()
scratch_dir = Path(sys.argv[3]).resolve()
sample = workspace / "sample.py"
sentinel_before = sentinel.read_bytes()
sample_before = sample.read_bytes()

registry = load_skill_registry(
    base_dir=workspace,
    scratch_dir=scratch_dir,
    config={"hardware_profile": "low_vram_8gb"},
)
review = registry.skill("code_task").execute(
    {"action": "review", "targets": ["sample.py"]}
)
diagnostics = review.get("data", {}).get("diagnostics", [])
codes = sorted(
    item.get("code")
    for item in diagnostics
    if isinstance(item, dict) and isinstance(item.get("code"), str)
)

escape_path = os.path.relpath(sentinel, workspace)
denied = registry.skill("file_reader").execute({"file_path": escape_path})
shell = registry.skill("shell")
git_reader = registry.skill("git_reader")
escape_attempts = {
    "shell_status": shell.execute({"command": "git status"}),
    "shell_diff": shell.execute({"command": "git diff"}),
    "shell_write": shell.execute(
        {"command": shlex.join(["tree", "-o", escape_path, "."])}
    ),
    "git_status": git_reader.execute({"command": "status"}),
    "git_diff": git_reader.execute({"command": "diff"}),
}
secret = sentinel_before.decode("utf-8")

if not review.get("ok") or review.get("status") != "succeeded":
    raise SystemExit(f"installed code review failed: {review!r}")
if "PYSEC001" not in codes:
    raise SystemExit(f"installed code review missed PYSEC001: {codes!r}")
if denied.get("ok") is not False:
    raise SystemExit("installed file_reader escaped the workspace")
for name, result in escape_attempts.items():
    if result.get("ok") is not False:
        raise SystemExit(f"installed {name} escaped the workspace: {result!r}")
    if secret in json.dumps(result, ensure_ascii=False):
        raise SystemExit(f"installed {name} exposed the external sentinel")
if sentinel.read_bytes() != sentinel_before or sample.read_bytes() != sample_before:
    raise SystemExit("installed probe mutated its workspace or sentinel")

print(
    json.dumps(
        {
            "diagnostic_codes": codes,
            "escape_denied": True,
            "process_escape_denied": sorted(escape_attempts),
            "status": "ok",
        },
        sort_keys=True,
    )
)
"""

EXTENSION_BOOTSTRAP_PROBE_SOURCE = """\
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import agent
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext
from agent.tools.contracts import ToolAdapter, ToolDescriptor, ToolInvocation, ToolResult, ToolStatus
from agent.tools.extension_catalog_service import ExtensionCatalogService
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage
from agent.tools.extension_bootstrap import ApplicationExtensionBootstrap
from agent.tools.workspace_extensions_service import WorkspaceExtensionService


app_home = Path(sys.argv[1]).resolve()
workspace = Path(sys.argv[2]).resolve()
checkout = Path(sys.argv[3]).resolve()
workspace.mkdir(parents=True, exist_ok=True)
extension_dir = workspace.parent / "extension-source"
extension_dir.mkdir(parents=True, exist_ok=True)
manifest = extension_dir / "manifest.json"
manifest.write_text(
    json.dumps(
        {
            "id": "wheel.extension",
            "version": "1.0.0",
            "protocol_version": "1.0",
            "transport": "stdio",
            "entrypoint": ["${python}", "${extension_dir}/tool.py"],
            "timeout_seconds": 5,
            "tools": [{"name": "wheel_tool", "schema": {}, "capabilities": ["read"]}],
        }
    ),
    encoding="utf-8",
)
paths = AppPaths.discover(app_home, env={})
ConfigRepository(paths).initialize()
catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
catalog.add(manifest)
workspace_id = WorkspaceContext.create(workspace).workspace_id
service = WorkspaceExtensionService.for_workspace(paths, workspace_id, catalog)
service.enable("wheel.extension")
service.grant("wheel.extension", "read")
process_calls = []


class BuiltinProbeAdapter(ToolAdapter):
    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return (ToolDescriptor("echo", "builtin", schema={}),)

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        return ToolResult(invocation_id=invocation.invocation_id, status=ToolStatus.SUCCEEDED)


def forbidden(name):
    def fail(*args, **kwargs):
        process_calls.append(name)
        raise AssertionError(name)
    return fail


with patch.object(subprocess, "Popen", forbidden("Popen")), \
     patch.object(subprocess, "run", forbidden("run")), \
     patch.object(os, "system", forbidden("system")), \
     patch.object(asyncio, "create_subprocess_exec", forbidden("create_subprocess_exec")), \
     patch.object(asyncio, "create_subprocess_shell", forbidden("create_subprocess_shell")):
    result = ApplicationExtensionBootstrap(paths, workspace_id, workspace).build(
        BuiltinProbeAdapter()
    )
    adapter = result.registry._descriptors_cache["wheel_tool"][0]
    payload = {
        "tool": "wheel_tool",
        "cwd_ok": adapter.cwd == workspace,
        "builtins": "echo" in result.registry.names(),
        "process_calls": process_calls,
        "checkout_import": checkout in Path(agent.__file__).resolve().parents,
    }

print(json.dumps(payload, sort_keys=True))
"""


class VerificationError(RuntimeError):
    """Raised when the installed artifact violates a distribution invariant."""


@dataclass(frozen=True)
class CommandResult:
    name: str
    stdout: str
    stderr: str


def _emit_failure_annotation(message: str) -> None:
    compact = " ".join(message.split())[:1200]
    escaped = (
        compact.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
    )
    print(
        f"::error title=Installed wheel acceptance::{escaped}",
        file=sys.stderr,
    )


@dataclass(frozen=True)
class InstallationMode:
    name: str
    system_site_packages: bool
    install_dependencies: bool
    acceptance: bool


def installation_mode(offline_diagnostic: bool = False) -> InstallationMode:
    if offline_diagnostic:
        return InstallationMode(
            name="offline-diagnostic",
            system_site_packages=True,
            install_dependencies=False,
            acceptance=False,
        )
    return InstallationMode(
        name="clean-acceptance",
        system_site_packages=False,
        install_dependencies=True,
        acceptance=True,
    )


def installed_cli_commands(
    executable: Path,
    workspace: Path,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return (
        ("version", (str(executable), "--version")),
        ("config-init", (str(executable), "config", "init")),
        ("doctor", (str(executable), "doctor", "--json")),
        (
            "run",
            (
                str(executable),
                "run",
                "--json",
                "--workspace",
                str(workspace),
                "oi",
            ),
        ),
    )


def snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    """Capture content and write-sensitive metadata for every regular file."""

    if not root.exists():
        return {}
    snapshot: dict[str, tuple[int, int, str]] = {}
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        stat = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        snapshot[path.relative_to(root).as_posix()] = (
            stat.st_size,
            stat.st_mtime_ns,
            digest,
        )
    return snapshot


def parse_json_output(result: CommandResult) -> dict[str, Any]:
    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        raise VerificationError(
            f"{result.name} não produziu JSON puro: {result.stdout!r}"
        ) from exc
    if not isinstance(payload, dict) or not payload:
        raise VerificationError(f"{result.name} deve produzir um objeto JSON não vazio.")
    return payload


def _run(
    name: str,
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: int = 180,
) -> CommandResult:
    print(f"[installed-gate] {name}...", flush=True)
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(environment) if environment is not None else None,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise VerificationError(
            f"{name} excedeu o timeout de {timeout_seconds}s."
        ) from exc
    result = CommandResult(name, completed.stdout, completed.stderr)
    if completed.returncode != 0:
        raise VerificationError(
            f"{name} falhou com exit code {completed.returncode}.\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return result


def _venv_executable(environment_dir: Path, name: str) -> Path:
    scripts = environment_dir / ("Scripts" if os.name == "nt" else "bin")
    suffix = ".exe" if os.name == "nt" else ""
    return scripts / f"{name}{suffix}"


def wheel_install_command(
    venv_python: Path,
    wheel: Path,
    mode: InstallationMode,
) -> tuple[str, ...]:
    command = [
        str(venv_python),
        "-m",
        "pip",
        "install",
    ]
    if not mode.install_dependencies:
        command.extend(("--no-deps", "--force-reinstall"))
    command.extend(
        (
            "--no-cache-dir",
            "--disable-pip-version-check",
            "--progress-bar",
            "off",
            str(wheel),
        )
    )
    return tuple(command)


def _site_packages(venv_python: Path, cwd: Path, environment: Mapping[str, str]) -> Path:
    code = "import sysconfig; print(sysconfig.get_paths()['purelib'])"
    result = _run(
        "locate-site-packages",
        (str(venv_python), "-c", code),
        cwd=cwd,
        environment=environment,
    )
    return Path(result.stdout.strip()).resolve()


def _build_wheel(
    project_root: Path,
    wheel_dir: Path,
    python: Path,
    *,
    no_build_isolation: bool,
) -> Path:
    command = [
        str(python),
        "-m",
        "pip",
        "wheel",
        "--no-deps",
    ]
    if no_build_isolation:
        command.append("--no-build-isolation")
    command.extend(
        [
            "--no-cache-dir",
            "--disable-pip-version-check",
            "--progress-bar",
            "off",
            "--wheel-dir",
            str(wheel_dir),
            str(project_root),
        ]
    )
    _run(
        "build-wheel",
        command,
        cwd=wheel_dir,
    )
    wheels = sorted(wheel_dir.glob("local_llm_agent-*.whl"))
    if len(wheels) != 1:
        raise VerificationError(
            f"Esperado exatamente um wheel da aplicação; encontrados: {wheels}"
        )
    return wheels[0]


def _install_wheel(
    wheel: Path,
    environment_dir: Path,
    external_cwd: Path,
    mode: InstallationMode,
) -> tuple[Path, Path]:
    print(f"[installed-gate] create-venv ({mode.name})...", flush=True)
    venv.EnvBuilder(
        with_pip=True,
        system_site_packages=mode.system_site_packages,
        clear=True,
    ).create(environment_dir)
    venv_python = _venv_executable(environment_dir, "python")
    _run(
        "install-wheel",
        wheel_install_command(venv_python, wheel, mode),
        cwd=external_cwd,
    )
    entrypoint = _venv_executable(environment_dir, "llm-agent")
    if not entrypoint.is_file():
        raise VerificationError(f"Console script não foi instalado: {entrypoint}")
    return venv_python, entrypoint


def _runtime_environment(app_home: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["LLM_AGENT_HOME"] = str(app_home)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUTF8"] = "1"
    environment["NO_COLOR"] = "1"
    return environment


def _verify_import_origin(
    venv_python: Path,
    site_packages: Path,
    cwd: Path,
    environment: Mapping[str, str],
) -> None:
    code = "from pathlib import Path; import agent; print(Path(agent.__file__).resolve())"
    result = _run(
        "installed-import",
        (str(venv_python), "-c", code),
        cwd=cwd,
        environment=environment,
    )
    imported = Path(result.stdout.strip()).resolve()
    try:
        imported.relative_to(site_packages)
    except ValueError as exc:
        raise VerificationError(
            f"'agent' foi importado fora do site-packages isolado: {imported}"
        ) from exc


def _verify_declared_dependencies(
    venv_python: Path,
    site_packages: Path,
    cwd: Path,
    environment: Mapping[str, str],
    mode: InstallationMode,
) -> None:
    modules = json.dumps(DECLARED_RUNTIME_IMPORTS)
    code = (
        "import importlib, json; "
        f"names = {modules}; "
        "print(json.dumps({name: importlib.import_module(name).__file__ "
        "for name in names}, sort_keys=True))"
    )
    result = _run(
        "declared-dependencies",
        (str(venv_python), "-c", code),
        cwd=cwd,
        environment=environment,
    )
    origins = parse_json_output(result)
    if not mode.acceptance:
        return
    for name in DECLARED_RUNTIME_IMPORTS:
        raw_origin = origins.get(name)
        if not isinstance(raw_origin, str):
            raise VerificationError(f"Dependência declarada sem origem válida: {name}")
        origin = Path(raw_origin).resolve()
        try:
            origin.relative_to(site_packages)
        except ValueError as exc:
            raise VerificationError(
                f"Dependência '{name}' não foi instalada no venv limpo: {origin}"
            ) from exc


def _verify_installed_probe(
    venv_python: Path,
    workspace: Path,
    sentinel: Path,
    scratch_dir: Path,
    probe_script: Path,
    environment: Mapping[str, str],
) -> None:
    result = _run(
        "installed-offline-probe",
        (
            str(venv_python),
            str(probe_script),
            str(workspace),
            str(sentinel),
            str(scratch_dir),
        ),
        cwd=probe_script.parent,
        environment=environment,
    )
    payload = parse_json_output(result)
    if payload.get("status") != "ok":
        raise VerificationError("Probe instalado não reportou status=ok.")
    if payload.get("escape_denied") is not True:
        raise VerificationError("Probe instalado não confirmou confinamento ao workspace.")
    codes = payload.get("diagnostic_codes")
    if not isinstance(codes, list) or "PYSEC001" not in codes:
        raise VerificationError("Probe instalado não confirmou análise de código real.")
    process_guards = payload.get("process_escape_denied")
    if process_guards != [
        "git_diff",
        "git_status",
        "shell_diff",
        "shell_status",
        "shell_write",
    ]:
        raise VerificationError(
            "Probe instalado não confirmou confinamento de ShellSkill/GitSkill."
        )


def _verify_extension_aware_bootstrap(
    venv_python: Path,
    app_home: Path,
    workspace: Path,
    project_root: Path,
    cwd: Path,
    environment: Mapping[str, str],
) -> None:
    result = _run(
        "installed-extension-aware-bootstrap",
        (
            str(venv_python),
            "-I",
            "-c",
            EXTENSION_BOOTSTRAP_PROBE_SOURCE,
            str(app_home),
            str(workspace),
            str(project_root),
        ),
        cwd=cwd,
        environment=environment,
    )
    payload = parse_json_output(result)
    if payload.get("tool") != "wheel_tool":
        raise VerificationError("Wheel nÃ£o publicou o descriptor da extension.")
    if payload.get("cwd_ok") is not True:
        raise VerificationError("Adapter instalado recebeu cwd incorreto.")
    if payload.get("builtins") is not True:
        raise VerificationError("Bootstrap instalado perdeu builtins.")
    if payload.get("process_calls") != []:
        raise VerificationError("Bootstrap instalado iniciou subprocesso.")
    if payload.get("checkout_import") is not False:
        raise VerificationError("Bootstrap instalado importou o checkout.")


def _verify_version(result: CommandResult) -> None:
    if not re.search(r"\b\d+\.\d+\.\d+\b", result.stdout):
        raise VerificationError(f"--version não informou versão semântica: {result.stdout!r}")


def _verify_config(app_home: Path) -> None:
    config_file = app_home / "config" / "config.json"
    if not config_file.is_file():
        raise VerificationError(f"config init não criou {config_file}")
    try:
        document = json.loads(config_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise VerificationError("config init criou JSON inválido.") from exc
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise VerificationError("config init não criou configuração schema_version=1.")


def _verify_greeting(payload: Mapping[str, Any]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False).casefold()
    if not any(term in rendered for term in ("olá", "ola", "ajudar")):
        raise VerificationError("run headless não retornou a resposta trivial esperada.")
    if payload.get("success") is False or payload.get("ok") is False:
        raise VerificationError("run headless reportou falha.")


def verify_installed_package(
    project_root: Path = ROOT,
    python: Path = Path(sys.executable),
    *,
    no_build_isolation: bool = False,
    offline_diagnostic: bool = False,
) -> None:
    project_root = project_root.resolve()
    mode = installation_mode(offline_diagnostic)
    with tempfile.TemporaryDirectory(prefix="llm-agent-installed-") as raw_temp:
        temp = Path(raw_temp)
        wheel_dir = temp / "wheel"
        environment_dir = temp / "venv"
        external_cwd = temp / "outside-checkout"
        workspace = temp / "workspace"
        app_home = temp / "app-home"
        wheel_dir.mkdir()
        external_cwd.mkdir()
        workspace.mkdir()
        sentinel = external_cwd / "sentinel.txt"
        probe_script = external_cwd / "installed_probe.py"
        sample = workspace / "sample.py"
        sentinel.write_text("outside-workspace-sentinel\n", encoding="utf-8")
        probe_script.write_text(INSTALLED_PROBE_SOURCE, encoding="utf-8")
        sample.write_text(
            "def evaluate(expression: str) -> object:\n"
            "    return eval(expression)\n",
            encoding="utf-8",
        )

        wheel = _build_wheel(
            project_root,
            wheel_dir,
            python.resolve(),
            no_build_isolation=no_build_isolation,
        )
        cwd_before = snapshot_tree(external_cwd)
        workspace_before = snapshot_tree(workspace)
        venv_python, entrypoint = _install_wheel(
            wheel,
            environment_dir,
            external_cwd,
            mode,
        )
        runtime_environment = _runtime_environment(app_home)
        site_packages = _site_packages(
            venv_python,
            external_cwd,
            runtime_environment,
        )
        site_before = snapshot_tree(site_packages)
        _verify_declared_dependencies(
            venv_python,
            site_packages,
            external_cwd,
            runtime_environment,
            mode,
        )
        _verify_installed_probe(
            venv_python,
            workspace,
            sentinel,
            app_home / "probe-scratch",
            probe_script,
            runtime_environment,
        )
        _verify_extension_aware_bootstrap(
            venv_python,
            temp / "extension-app-home",
            temp / "extension-workspace",
            project_root,
            external_cwd,
            runtime_environment,
        )
        _verify_import_origin(
            venv_python,
            site_packages,
            external_cwd,
            runtime_environment,
        )

        results: dict[str, CommandResult] = {}
        for name, command in installed_cli_commands(entrypoint, workspace):
            results[name] = _run(
                name,
                command,
                cwd=external_cwd,
                environment=runtime_environment,
            )

        _verify_version(results["version"])
        _verify_config(app_home)
        parse_json_output(results["doctor"])
        _verify_greeting(parse_json_output(results["run"]))
        if snapshot_tree(external_cwd) != cwd_before:
            raise VerificationError("A CLI escreveu no diretório externo de execução.")
        if snapshot_tree(workspace) != workspace_before:
            raise VerificationError("O artefato instalado modificou o workspace no probe.")
        if snapshot_tree(site_packages) != site_before:
            raise VerificationError("A CLI escreveu no site-packages após a instalação.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument(
        "--no-build-isolation",
        action="store_true",
        help="usa build requirements já instalados (útil em ambientes sem rede)",
    )
    parser.add_argument(
        "--offline-diagnostic",
        action="store_true",
        help=(
            "reutiliza dependências do Python base e instala o wheel sem resolvê-las; "
            "diagnóstico local mais fraco, não é um gate de aceitação"
        ),
    )
    arguments = parser.parse_args(argv)
    try:
        verify_installed_package(
            arguments.project_root,
            arguments.python,
            no_build_isolation=arguments.no_build_isolation,
            offline_diagnostic=arguments.offline_diagnostic,
        )
    except VerificationError as exc:
        print(f"Installed package verification failed: {exc}", file=sys.stderr)
        _emit_failure_annotation(str(exc))
        return 1
    if arguments.offline_diagnostic:
        print(
            "Offline installed-package diagnostic passed "
            "(dependency completeness was not verified; this is not an acceptance gate)."
        )
    else:
        print("Installed package clean acceptance passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
