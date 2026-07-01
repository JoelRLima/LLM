"""
Diagnóstico de saúde do LLM Agent.

Executa verificações de integridade sobre configuração, memória, workspace,
skills, permissões e arquivos de log/métricas, gerando:

1. Relatório visual no terminal (✅ / ⚠️ / ❌).
2. Arquivo health_report.json com detalhes de cada verificação.

Uso: python -m agent.health_check
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys
import tempfile
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------- #
# Constantes
# --------------------------------------------------------------------------- #

# Raiz do projeto: assume-se que este módulo vive em `agent/health_check.py`,
# logo a raiz do projeto é o diretório pai do pacote `agent`.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

CONFIG_PATH: Path = PROJECT_ROOT / "config.json"
MEMORY_PATH: Path = PROJECT_ROOT / "agent_memory.json"
MEMORY_BACKUP_DIR: Path = PROJECT_ROOT / "memory_backups"
MEMORY_RESTORE_DIR: Path = MEMORY_BACKUP_DIR / "restore"
TEMP_ANALYSIS_DIR: Path = PROJECT_ROOT / ".temp_analysis"
LOG_FILE: Path = PROJECT_ROOT / "agent.log"
METRICS_FILE: Path = PROJECT_ROOT / "agent_metrics.jsonl"
HEALTH_REPORT_PATH: Path = PROJECT_ROOT / "health_report.json"

REQUIRED_CONFIG_KEYS: List[str] = [
    "api_url",
    "model",
    "temperature",
    "max_tokens",
    "timeout",
    "default_system_prompt",
]

EXPECTED_MEMORY_SECTIONS: List[str] = [
    "project_map",
    "key_findings",
    "analyzed_files",
    "file_summaries",
    "file_hashes",
]

ESSENTIAL_SKILLS: List[str] = [
    "file_reader",
    "file_writer",
    "python_executor",
    "grep",
    "directory_lister",
]

LOG_SIZE_WARNING_BYTES: int = 10 * 1024 * 1024  # 10 MB

STATUS_OK = "ok"
STATUS_WARNING = "warning"
STATUS_ERROR = "error"

STATUS_ICON = {
    STATUS_OK: "✅",
    STATUS_WARNING: "⚠️",
    STATUS_ERROR: "❌",
}


# --------------------------------------------------------------------------- #
# Estrutura de dados do resultado
# --------------------------------------------------------------------------- #

@dataclass
class CheckResult:
    """Resultado padronizado de uma verificação individual."""

    name: str
    status: str = STATUS_OK
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _ensure_sys_path() -> None:
    """Garante que a raiz do projeto esteja em sys.path.

    Isso é necessário porque diversos módulos do projeto (`config.py`,
    `logger.py`) são importados como módulos de topo (ex.: `import config`),
    assumindo que a raiz do projeto está no `sys.path`. Quando este módulo é
    executado via `python -m agent.health_check` a partir da raiz do
    projeto, isso já ocorre naturalmente, mas garantimos aqui por segurança
    (ex.: quando importado de outro diretório).
    """
    root_str = str(PROJECT_ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


def _safe_check(name: str, func) -> CheckResult:
    """Executa uma função de verificação isolando qualquer exceção.

    Garante que uma falha inesperada em uma verificação não impeça a
    execução das demais, convertendo a exceção em um CheckResult de erro.
    """
    try:
        result = func()
        if not isinstance(result, CheckResult):
            # Segurança extra: se uma verificação esquecer de retornar
            # um CheckResult, encapsulamos o valor bruto.
            return CheckResult(
                name=name,
                status=STATUS_WARNING,
                message="Verificação não retornou um CheckResult válido.",
                details={"raw_result": str(result)},
            )
        return result
    except Exception as exc:  # noqa: BLE001 - queremos capturar tudo aqui
        return CheckResult(
            name=name,
            status=STATUS_ERROR,
            message=f"Falha inesperada durante a verificação: {exc}",
            details={"traceback": traceback.format_exc()},
        )


# --------------------------------------------------------------------------- #
# 1. Python
# --------------------------------------------------------------------------- #

def check_python_version() -> CheckResult:
    """Verifica se a versão do Python em uso é >= 3.10."""
    major, minor = sys.version_info.major, sys.version_info.minor
    version_str = f"{major}.{minor}.{sys.version_info.micro}"

    if (major, minor) >= (3, 10):
        return CheckResult(
            name="Versão do Python",
            status=STATUS_OK,
            message=f"Python {version_str} atende ao mínimo exigido (>=3.10).",
            details={"version": version_str},
        )

    return CheckResult(
        name="Versão do Python",
        status=STATUS_ERROR,
        message=f"Python {version_str} é inferior ao mínimo exigido (3.10).",
        details={"version": version_str},
    )


# --------------------------------------------------------------------------- #
# 2. Configuração (config.json)
# --------------------------------------------------------------------------- #

def check_config() -> CheckResult:
    """Verifica existência, validade e chaves obrigatórias de config.json.

    Reaproveita a função pública `carregar_config` de `config.py` (raiz do
    projeto) em vez de duplicar a lógica de validação de tipos/limites.
    """
    details: Dict[str, Any] = {"path": str(CONFIG_PATH)}

    if not CONFIG_PATH.exists():
        return CheckResult(
            name="Configuração (config.json)",
            status=STATUS_ERROR,
            message="Arquivo 'config.json' não encontrado.",
            details=details,
        )

    # Passo 1: validar que é JSON bem formado, de forma independente da
    # lógica de fallback de `carregar_config` (que não levanta exceção
    # para tipos inválidos, apenas substitui por padrões).
    try:
        raw_text = CONFIG_PATH.read_text(encoding="utf-8")
        raw_config = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return CheckResult(
            name="Configuração (config.json)",
            status=STATUS_ERROR,
            message=f"'config.json' não é um JSON válido: {exc}",
            details=details,
        )
    except Exception as exc:
        return CheckResult(
            name="Configuração (config.json)",
            status=STATUS_ERROR,
            message=f"Erro ao ler 'config.json': {exc}",
            details=details,
        )

    # Passo 2: chaves obrigatórias presentes no arquivo bruto.
    missing_keys = [k for k in REQUIRED_CONFIG_KEYS if k not in raw_config]
    details["missing_keys"] = missing_keys
    details["present_keys"] = list(raw_config.keys())

    # Passo 3: reutiliza a lógica pública de carregamento/validação de
    # tipos e limites já existente em config.py.
    try:
        _ensure_sys_path()
        config_module = importlib.import_module("config")
        loaded_config = config_module.carregar_config(str(CONFIG_PATH))
        details["loaded_ok"] = True
    except Exception as exc:
        details["loaded_ok"] = False
        details["load_error"] = str(exc)
        return CheckResult(
            name="Configuração (config.json)",
            status=STATUS_ERROR,
            message=f"'carregar_config' falhou ao processar o arquivo: {exc}",
            details=details,
        )

    if missing_keys:
        return CheckResult(
            name="Configuração (config.json)",
            status=STATUS_WARNING,
            message=(
                "Arquivo válido, mas faltam chaves obrigatórias "
                f"(fallbacks serão usados): {', '.join(missing_keys)}."
            ),
            details=details,
        )

    return CheckResult(
        name="Configuração (config.json)",
        status=STATUS_OK,
        message="Arquivo de configuração válido e completo.",
        details=details,
    )


# --------------------------------------------------------------------------- #
# 3. Memória (agent_memory.json) + backups
# --------------------------------------------------------------------------- #

def check_memory() -> CheckResult:
    """Verifica existência, validade e seções esperadas da memória persistente."""
    details: Dict[str, Any] = {"path": str(MEMORY_PATH)}

    if not MEMORY_PATH.exists():
        return CheckResult(
            name="Memória (agent_memory.json)",
            status=STATUS_WARNING,
            message="Arquivo 'agent_memory.json' não encontrado (memória ainda vazia).",
            details=details,
        )

    try:
        raw_text = MEMORY_PATH.read_text(encoding="utf-8")
        memory_data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return CheckResult(
            name="Memória (agent_memory.json)",
            status=STATUS_ERROR,
            message=f"'agent_memory.json' não é um JSON válido: {exc}",
            details=details,
        )
    except Exception as exc:
        return CheckResult(
            name="Memória (agent_memory.json)",
            status=STATUS_ERROR,
            message=f"Erro ao ler 'agent_memory.json': {exc}",
            details=details,
        )

    missing_sections = [s for s in EXPECTED_MEMORY_SECTIONS if s not in memory_data]
    details["missing_sections"] = missing_sections
    details["present_sections"] = list(memory_data.keys())

    status = STATUS_OK
    message = "Memória válida e com as seções esperadas."
    if missing_sections:
        status = STATUS_WARNING
        message = f"Memória válida, mas faltam seções: {', '.join(missing_sections)}."

    # Verificação dos backups em memory_backups/
    backup_info = _check_memory_backups()
    details["backups"] = backup_info

    if backup_info["invalid_files"]:
        status = STATUS_WARNING if status == STATUS_OK else status
        message += (
            f" {len(backup_info['invalid_files'])} backup(s) com JSON inválido."
        )

    return CheckResult(
        name="Memória (agent_memory.json)",
        status=status,
        message=message,
        details=details,
    )


def _check_memory_backups() -> Dict[str, Any]:
    """Verifica se os backups em memory_backups/ existem e são JSON válidos."""
    info: Dict[str, Any] = {
        "dir_exists": MEMORY_BACKUP_DIR.exists(),
        "total_backups": 0,
        "valid_files": [],
        "invalid_files": [],
    }

    if not MEMORY_BACKUP_DIR.exists():
        return info

    try:
        backup_files = sorted(
            p for p in MEMORY_BACKUP_DIR.iterdir()
            if p.is_file() and p.name.endswith(".bak")
        )
    except Exception as exc:
        info["error"] = str(exc)
        return info

    info["total_backups"] = len(backup_files)

    for bak in backup_files:
        try:
            json.loads(bak.read_text(encoding="utf-8"))
            info["valid_files"].append(bak.name)
        except Exception as exc:
            info["invalid_files"].append({"file": bak.name, "error": str(exc)})

    return info


# --------------------------------------------------------------------------- #
# 4. Hashes de arquivos
# --------------------------------------------------------------------------- #

def check_file_hashes() -> CheckResult:
    """Compara os hashes SHA256 registrados em memória com os arquivos atuais."""
    details: Dict[str, Any] = {}

    if not MEMORY_PATH.exists():
        return CheckResult(
            name="Hashes de arquivos",
            status=STATUS_WARNING,
            message="Sem 'agent_memory.json' para verificar hashes.",
            details=details,
        )

    try:
        memory_data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return CheckResult(
            name="Hashes de arquivos",
            status=STATUS_ERROR,
            message=f"Não foi possível ler a memória para checar hashes: {exc}",
            details=details,
        )

    file_hashes = memory_data.get("file_hashes", {})
    if not file_hashes:
        return CheckResult(
            name="Hashes de arquivos",
            status=STATUS_OK,
            message="Nenhum hash registrado em memória (nada a verificar).",
            details=details,
        )

    matched: List[str] = []
    mismatched: List[Dict[str, str]] = []
    missing_files: List[str] = []

    for rel_path, expected_hash in file_hashes.items():
        target = PROJECT_ROOT / rel_path
        if not target.exists():
            missing_files.append(rel_path)
            continue
        try:
            actual_hash = _sha256_of_file(target)
        except Exception as exc:
            mismatched.append({"file": rel_path, "error": str(exc)})
            continue

        if actual_hash == expected_hash:
            matched.append(rel_path)
        else:
            mismatched.append(
                {"file": rel_path, "expected": expected_hash, "actual": actual_hash}
            )

    details["matched"] = matched
    details["mismatched"] = mismatched
    details["missing_files"] = missing_files

    if mismatched or missing_files:
        return CheckResult(
            name="Hashes de arquivos",
            status=STATUS_WARNING,
            message=(
                f"{len(mismatched)} hash(es) divergente(s) e "
                f"{len(missing_files)} arquivo(s) ausente(s) de {len(file_hashes)} registrados."
            ),
            details=details,
        )

    return CheckResult(
        name="Hashes de arquivos",
        status=STATUS_OK,
        message=f"Todos os {len(matched)} hashes registrados conferem.",
        details=details,
    )


def _sha256_of_file(path: Path, chunk_size: int = 65536) -> str:
    """Calcula o hash SHA256 de um arquivo em blocos, sem carregá-lo por completo."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


# --------------------------------------------------------------------------- #
# 5. Diretórios órfãos
# --------------------------------------------------------------------------- #

def check_orphan_dirs() -> CheckResult:
    """Verifica a existência de diretórios técnicos que podem estar órfãos."""
    details: Dict[str, Any] = {}
    warnings: List[str] = []

    # .temp_analysis/
    if TEMP_ANALYSIS_DIR.exists():
        try:
            contents = list(TEMP_ANALYSIS_DIR.iterdir())
        except Exception as exc:
            contents = []
            details["temp_analysis_error"] = str(exc)
        details["temp_analysis_exists"] = True
        details["temp_analysis_file_count"] = len(contents)
        if contents:
            warnings.append(
                f"'.temp_analysis/' existe e contém {len(contents)} item(ns) "
                "(possível lixo de análises antigas)."
            )
    else:
        details["temp_analysis_exists"] = False

    # memory_backups/restore/
    if MEMORY_RESTORE_DIR.exists():
        try:
            restore_entries = list(MEMORY_RESTORE_DIR.iterdir())
        except Exception as exc:
            restore_entries = []
            details["restore_dir_error"] = str(exc)
        details["restore_dir_exists"] = True
        details["restore_dir_entry_count"] = len(restore_entries)
        warnings.append(
            f"'memory_backups/restore/' existe com {len(restore_entries)} "
            "entrada(s) — provavelmente um restore point de uma tarefa "
            "interrompida que não foi limpo."
        )
    else:
        details["restore_dir_exists"] = False

    if warnings:
        return CheckResult(
            name="Diretórios órfãos",
            status=STATUS_WARNING,
            message=" ".join(warnings),
            details=details,
        )

    return CheckResult(
        name="Diretórios órfãos",
        status=STATUS_OK,
        message="Nenhum diretório órfão encontrado.",
        details=details,
    )


# --------------------------------------------------------------------------- #
# 6. Permissões
# --------------------------------------------------------------------------- #

def check_permissions() -> CheckResult:
    """Testa permissões de leitura/escrita na raiz do projeto e pastas técnicas."""
    details: Dict[str, Any] = {}
    problems: List[str] = []

    # Raiz do projeto
    root_ok, root_err = _test_write_read_delete(PROJECT_ROOT)
    details["project_root_writable"] = root_ok
    if not root_ok:
        problems.append(f"raiz do projeto ({root_err})")

    # .temp_analysis/ (se existir)
    if TEMP_ANALYSIS_DIR.exists():
        temp_ok, temp_err = _test_write_read_delete(TEMP_ANALYSIS_DIR)
        details["temp_analysis_writable"] = temp_ok
        if not temp_ok:
            problems.append(f".temp_analysis/ ({temp_err})")
    else:
        details["temp_analysis_writable"] = None  # N/A

    # memory_backups/
    if MEMORY_BACKUP_DIR.exists():
        backups_ok, backups_err = _test_write_read_delete(MEMORY_BACKUP_DIR)
        details["memory_backups_writable"] = backups_ok
        if not backups_ok:
            problems.append(f"memory_backups/ ({backups_err})")
    else:
        details["memory_backups_writable"] = None  # N/A

    if problems:
        return CheckResult(
            name="Permissões de leitura/escrita",
            status=STATUS_ERROR,
            message="Problemas de permissão em: " + "; ".join(problems),
            details=details,
        )

    return CheckResult(
        name="Permissões de leitura/escrita",
        status=STATUS_OK,
        message="Leitura e escrita funcionando normalmente nos diretórios verificados.",
        details=details,
    )


def _test_write_read_delete(directory: Path) -> "tuple[bool, Optional[str]]":
    """Cria um arquivo temporário no diretório informado, lê e apaga.

    Retorna (sucesso, mensagem_de_erro_ou_None).
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".health_check_", suffix=".tmp", dir=str(directory)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("health_check_probe")
            content = Path(tmp_name).read_text(encoding="utf-8")
            if content != "health_check_probe":
                return False, "conteúdo lido difere do escrito"
        finally:
            try:
                os.remove(tmp_name)
            except OSError:
                pass
        return True, None
    except Exception as exc:
        return False, str(exc)


# --------------------------------------------------------------------------- #
# 7. Skills
# --------------------------------------------------------------------------- #

def check_skills() -> CheckResult:
    """Carrega todas as skills registradas e valida a presença das essenciais."""
    details: Dict[str, Any] = {}

    try:
        _ensure_sys_path()
        skills_module = importlib.import_module("agent.skills")
        load_all_skills = getattr(skills_module, "load_all_skills")
        loaded_skills = load_all_skills()
    except Exception as exc:
        return CheckResult(
            name="Skills carregadas",
            status=STATUS_ERROR,
            message=f"Falha ao carregar skills via 'load_all_skills()': {exc}",
            details={"traceback": traceback.format_exc()},
        )

    skill_names: List[str] = []
    for skill in loaded_skills:
        try:
            skill_names.append(getattr(skill, "name"))
        except Exception:
            skill_names.append(f"<sem nome: {type(skill).__name__}>")

    details["loaded_skill_names"] = skill_names
    details["total_loaded"] = len(skill_names)

    missing_essential = [s for s in ESSENTIAL_SKILLS if s not in skill_names]
    details["missing_essential_skills"] = missing_essential

    status = STATUS_OK
    messages: List[str] = [f"{len(skill_names)} skill(s) carregada(s)."]

    if missing_essential:
        status = STATUS_ERROR
        messages.append(
            "Skills essenciais ausentes: " + ", ".join(missing_essential) + "."
        )

    # Testa a skill 'echo', se presente, para confirmar que o mecanismo
    # de execução de skills está funcional.
    echo_skill = next((s for s in loaded_skills if getattr(s, "name", None) == "echo"), None)
    if echo_skill is not None:
        try:
            probe_text = "health_check_ping"
            result = echo_skill.execute({"text": probe_text})
            details["echo_test_result"] = result if isinstance(result, dict) else str(result)
            if isinstance(result, dict) and result.get("ok") is True:
                messages.append("Teste da skill 'echo' bem-sucedido.")
            else:
                status = STATUS_WARNING if status == STATUS_OK else status
                messages.append(
                    "Skill 'echo' respondeu, mas o contrato de retorno "
                    "não indicou sucesso claro ('ok' != True)."
                )
        except Exception as exc:
            status = STATUS_WARNING if status == STATUS_OK else status
            messages.append(f"Falha ao testar a skill 'echo': {exc}")
    else:
        details["echo_test_result"] = None
        messages.append("Skill 'echo' não encontrada — teste de execução pulado.")

    return CheckResult(
        name="Skills carregadas",
        status=status,
        message=" ".join(messages),
        details=details,
    )


# --------------------------------------------------------------------------- #
# 8. Logs e métricas (informativo)
# --------------------------------------------------------------------------- #

def check_logs() -> CheckResult:
    """Reporta o tamanho dos arquivos de log e métricas (apenas informativo)."""
    details: Dict[str, Any] = {}
    warnings: List[str] = []

    for label, path in (("agent.log", LOG_FILE), ("agent_metrics.jsonl", METRICS_FILE)):
        if path.exists():
            try:
                size_bytes = path.stat().st_size
            except Exception as exc:
                details[label] = {"exists": True, "error": str(exc)}
                continue
            details[label] = {
                "exists": True,
                "size_bytes": size_bytes,
                "size_mb": round(size_bytes / (1024 * 1024), 2),
            }
            if size_bytes > LOG_SIZE_WARNING_BYTES:
                warnings.append(
                    f"'{label}' está grande ({details[label]['size_mb']} MB)."
                )
        else:
            details[label] = {"exists": False}

    if warnings:
        return CheckResult(
            name="Logs e métricas",
            status=STATUS_WARNING,
            message=" ".join(warnings),
            details=details,
        )

    return CheckResult(
        name="Logs e métricas",
        status=STATUS_OK,
        message="Tamanhos de log/métricas dentro do esperado (ou arquivos ausentes).",
        details=details,
    )


# --------------------------------------------------------------------------- #
# Orquestração do relatório
# --------------------------------------------------------------------------- #

def run_health_check(write_report: bool = True, verbose: bool = True) -> Dict[str, Any]:
    """Executa todas as verificações e monta o relatório final.

    Args:
        write_report: se True, grava o resultado em `health_report.json`
            na raiz do projeto.
        verbose: se True, imprime o relatório formatado no terminal.

    Returns:
        Um dicionário com o resumo e a lista de resultados de cada
        verificação (mesmo formato gravado em `health_report.json`).
    """
    _ensure_sys_path()

    checks = [
        ("python_version", check_python_version),
        ("config", check_config),
        ("memory", check_memory),
        ("file_hashes", check_file_hashes),
        ("orphan_dirs", check_orphan_dirs),
        ("permissions", check_permissions),
        ("skills", check_skills),
        ("logs", check_logs),
    ]

    results: List[CheckResult] = []
    for key, func in checks:
        results.append(_safe_check(key, func))

    total = len(results)
    n_ok = sum(1 for r in results if r.status == STATUS_OK)
    n_warning = sum(1 for r in results if r.status == STATUS_WARNING)
    n_error = sum(1 for r in results if r.status == STATUS_ERROR)
    problems = n_warning + n_error

    if problems == 0:
        summary = "✅ Sistema saudável."
    else:
        summary = (
            f"⚠️ Foram encontrados {problems} problema(s) "
            f"({n_error} erro(s), {n_warning} aviso(s)) em {total} verificação(ões)."
        )

    report: Dict[str, Any] = {
        "summary": summary,
        "total_checks": total,
        "ok": n_ok,
        "warnings": n_warning,
        "errors": n_error,
        "project_root": str(PROJECT_ROOT),
        "checks": [r.to_dict() for r in results],
    }

    if verbose:
        _print_report(report)

    if write_report:
        try:
            HEALTH_REPORT_PATH.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if verbose:
                print(f"\n📄 Relatório salvo em: {HEALTH_REPORT_PATH}")
        except Exception as exc:
            if verbose:
                print(f"\n⚠️ Não foi possível salvar 'health_report.json': {exc}")

    return report


def _print_report(report: Dict[str, Any]) -> None:
    """Imprime o relatório de saúde formatado no terminal."""
    print("=" * 70)
    print("🩺  RELATÓRIO DE SAÚDE DO AGENTE (health_check)")
    print("=" * 70)

    for check in report["checks"]:
        icon = STATUS_ICON.get(check["status"], "❓")
        print(f"\n{icon} {check['name']}")
        print(f"   {check['message']}")

    print("\n" + "-" * 70)
    print(report["summary"])
    print("-" * 70)


# --------------------------------------------------------------------------- #
# Integração sugerida com a CLI (apenas comentário — nenhum arquivo alterado)
# --------------------------------------------------------------------------- #
#
# Em `commands.py`, seria possível adicionar um comando `/doctor` assim:
#
#     elif comando in ("/doctor", "/diagnostico"):
#         from agent.health_check import run_health_check
#         run_health_check()
#
# E registrar a entrada correspondente na tabela exibida por `exibir_menu()`,
# por exemplo: `/doctor` ou `/diagnostico` -> "Executa o diagnóstico de
# saúde do agente (config, memória, skills, permissões etc.)".
#
# Nenhuma dessas mudanças foi aplicada a `commands.py` — este bloco é
# apenas documentação de como a integração poderia ser feita futuramente.


# --------------------------------------------------------------------------- #
# Execução via linha de comando
# --------------------------------------------------------------------------- #

def main() -> int:
    """Ponto de entrada para `python -m agent.health_check`.

    Returns:
        Código de saída do processo: 0 se saudável, 1 se houver erros/avisos.
    """
    report = run_health_check(write_report=True, verbose=True)
    return 0 if (report["errors"] == 0 and report["warnings"] == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
