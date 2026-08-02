"""
PlanValidator: diagnóstico somente-leitura de um plano.

Regra fundamental: `PlanValidator` NUNCA modifica o plano. Ele apenas
relata problemas através de um `ValidationReport`; cabe ao Orchestrator
decidir se aborta a tarefa, aciona o Replanner para os passos bloqueados,
ou segue em frente (para meros avisos).

Usado em dois pontos do pipeline (ver `agent/orchestrator.py`):
    1. Logo após o `PlanBuilder` gerar o plano (diagnóstico pré-otimização).
    2. Logo após o `PlanOptimizer` processar o plano (checagem
       pós-otimização, garantindo que nenhuma otimização introduziu um
       problema novo).

O Replanner (`agent/replan.py`) também reaproveita este validador para
checar os novos passos que ele mesmo propõe, antes de devolvê-los ao
`PlanExecutor` ou ao Orchestrator.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent.parsers import validate_tool_args
from agent.tools.invocation_gateway import ToolInvocationGateway


@dataclass(frozen=True)
class BlockedStep:
    """Um passo do plano que não pode ser executado como está."""
    index: int
    reason: str


@dataclass
class ValidationReport:
    """Resultado de uma chamada a `PlanValidator.validate()`."""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    blocked_steps: List[BlockedStep] = field(default_factory=list)


class PlanValidator:
    """Valida planos contra o schema das ferramentas, a lista de
    ferramentas permitidas para a tarefa, e um conjunto de heurísticas de
    segurança e consistência.

    Não possui efeitos colaterais e nunca altera o plano recebido.
    """

    def __init__(
        self,
        skills: Dict[str, Any],
        active_skills: Optional[List[str]] = None,
        allowed_capabilities: Optional[frozenset[str]] = None,
        tool_registry: Any = None,
    ) -> None:
        self.skills = skills
        self.active_skills = active_skills or []
        self.allowed_capabilities = allowed_capabilities
        self.tool_registry = tool_registry

    def validate(self, plan: Optional[List[Dict[str, Any]]]) -> ValidationReport:
        """Executa todas as checagens sobre `plan` e retorna um
        `ValidationReport` consolidado.

        `is_valid` é `False` apenas quando o plano está estruturalmente
        inutilizável (ausente, não é uma lista, vazio, ou todos os passos
        acabaram bloqueados) — nesses casos o Orchestrator deve abortar a
        tarefa sem tentar replanejar. Quando `is_valid` é `True` mas
        `blocked_steps` não está vazio, o plano ainda tem passos
        executáveis e o Orchestrator deve acionar o Replanner apenas para
        os passos bloqueados.
        """
        errors: List[str] = []
        warnings: List[str] = []
        blocked: List[BlockedStep] = []

        if plan is None or not isinstance(plan, list):
            errors.append("Plano ausente ou em formato inválido (esperada uma lista de passos).")
            return ValidationReport(is_valid=False, errors=errors, warnings=warnings, blocked_steps=blocked)

        if len(plan) == 0:
            errors.append("Plano vazio: nenhum passo para executar.")
            return ValidationReport(is_valid=False, errors=errors, warnings=warnings, blocked_steps=blocked)

        self._validate_schema_and_tools(plan, blocked)
        self._validate_analysis_notes(plan, blocked)
        self._validate_patch_without_read(plan, warnings)
        self._validate_consecutive_writes(plan, warnings)
        self._validate_inverted_dependencies(plan, blocked)

        is_valid = len(blocked) < len(plan)
        return ValidationReport(is_valid=is_valid, errors=errors, warnings=warnings, blocked_steps=blocked)

    # ------------------------------------------------------------------
    # Checagens individuais
    # ------------------------------------------------------------------

    @staticmethod
    def _step_args(step: Dict[str, Any]) -> Dict[str, Any]:
        args = step.get("args")
        return args if isinstance(args, dict) else {}

    def _validate_schema_and_tools(self, plan: List[Dict[str, Any]], blocked: List[BlockedStep]) -> None:
        """Valida, para cada passo: formato mínimo, existência da
        ferramenta, permissão (active_skills) e schema de argumentos."""
        for idx, step in enumerate(plan):
            problem = self._validate_step_schema(step)
            if problem:
                blocked.append(BlockedStep(idx, problem))

    def _validate_step_schema(self, step: Any) -> str | None:
        if not isinstance(step, dict) or "tool" not in step:
            return "Passo malformado: falta o campo 'tool'."
        tool = step.get("tool")
        tool_name = str(tool)
        args = step.get("args", {})
        args = args if isinstance(args, dict) else {}
        descriptor = self._descriptor(tool_name)
        if tool not in self.skills and descriptor is None:
            return f"Ferramenta '{tool}' não existe."
        if self.active_skills and tool not in self.active_skills and descriptor is None:
            return f"Ferramenta '{tool}' não está permitida para esta tarefa."
        if descriptor is not None:
            capability_error = self._capability_error(tool_name, descriptor)
            if capability_error:
                return capability_error
            try:
                ToolInvocationGateway._validate_arguments(descriptor, args)
                return None
            except ValueError as exc:
                return f"Schema inválido para '{tool}': {exc}"
        valid, error = validate_tool_args(tool_name, args, self.skills)
        return None if valid else f"Schema inválido para '{tool}': {error or ''}"

    def _descriptor(self, tool_name: str) -> Any:
        if self.tool_registry is None:
            return None
        try:
            return self.tool_registry.descriptor(tool_name)
        except KeyError:
            return None

    def _capability_error(self, tool_name: str, descriptor: Any) -> str | None:
        if self.allowed_capabilities is None:
            return None
        capabilities: frozenset[str] = getattr(descriptor, "capabilities", frozenset())
        missing = capabilities - self.allowed_capabilities
        if not missing:
            return None
        return f"Ferramenta '{tool_name}' requer capacidades não autorizadas: {', '.join(sorted(missing))}"

    def _validate_analysis_notes(self, plan: List[Dict[str, Any]], blocked: List[BlockedStep]) -> None:
        """Bloqueia passos que esvaziariam ou apagariam 'analysis_notes.md'."""
        for idx, step in enumerate(plan):
            if not isinstance(step, dict) or step.get("tool") != "file_writer":
                continue
            args = self._step_args(step)
            if "analysis_notes.md" not in str(args.get("file_path", "")):
                continue

            action = args.get("action", "write")
            if action == "delete_lines":
                blocked.append(BlockedStep(idx, "Passo apagaria linhas de 'analysis_notes.md'."))
                continue
            if action == "write":
                content = args.get("content")
                if content is None or str(content).strip() == "":
                    blocked.append(BlockedStep(idx, "Passo esvaziaria 'analysis_notes.md'."))

    def _validate_patch_without_read(self, plan: List[Dict[str, Any]], warnings: List[str]) -> None:
        """Aviso: um 'patch' em um arquivo sem que haja um 'file_reader'
        prévio desse mesmo arquivo em algum lugar do plano."""
        read_files = set()
        for idx, step in enumerate(plan):
            if not isinstance(step, dict):
                continue
            tool = step.get("tool")
            args = self._step_args(step)

            if tool == "file_reader":
                fp = args.get("file_path")
                if fp:
                    read_files.add(fp)
                continue

            if tool == "file_writer" and args.get("action") == "patch":
                fp = args.get("file_path")
                if fp and fp not in read_files:
                    warnings.append(
                        f"Passo {idx + 1}: patch em '{fp}' sem um file_reader prévio desse arquivo no plano."
                    )

    def _validate_consecutive_writes(self, plan: List[Dict[str, Any]], warnings: List[str]) -> None:
        """Aviso: duas escritas seguidas (sem nenhum outro passo entre elas)
        no mesmo arquivo — normalmente um sinal de que o plano poderia
        consolidar as duas edições em uma só."""
        last_write_file = None
        for idx, step in enumerate(plan):
            if not isinstance(step, dict) or step.get("tool") != "file_writer":
                last_write_file = None
                continue
            args = self._step_args(step)
            fp = args.get("file_path")
            if fp and fp == last_write_file:
                warnings.append(
                    f"Passo {idx + 1}: escrita consecutiva em '{fp}' (mesmo arquivo do passo imediatamente anterior)."
                )
            last_write_file = fp

    def _validate_inverted_dependencies(self, plan: List[Dict[str, Any]], blocked: List[BlockedStep]) -> None:
        """Bloqueia passos que leem/analisam um arquivo ANTES do passo
        file_writer que efetivamente o cria/produz no plano."""
        producers: Dict[str, int] = {}
        for idx, step in enumerate(plan):
            if not isinstance(step, dict) or step.get("tool") != "file_writer":
                continue
            args = self._step_args(step)
            fp = args.get("file_path")
            if fp and fp not in producers:
                producers[fp] = idx

        for idx, step in enumerate(plan):
            if not isinstance(step, dict):
                continue
            tool = step.get("tool")
            if tool not in ("file_reader", "code_analyzer"):
                continue
            args = self._step_args(step)
            fp = args.get("file_path") or args.get("target")
            if fp in producers and producers[fp] > idx:
                blocked.append(BlockedStep(
                    idx,
                    f"Dependência invertida: passo lê/analisa '{fp}' antes do passo {producers[fp] + 1}, que é quem o cria."
                ))
