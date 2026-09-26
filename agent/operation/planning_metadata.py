"""Pure planning metadata derived from the neutral operation catalog."""
from dataclasses import dataclass
from typing import Any, Dict, cast

from agent.capabilities import Capability
from agent.operation.catalog import BUILTIN_SKILL_SPECS


@dataclass(frozen=True)
class ToolMetadata:
    """Metadados normalizados que descrevem o comportamento de uma ferramenta."""
    cost: int
    reads_disk: bool
    writes_disk: bool
    modifies_workspace: bool
    cacheable: bool
    side_effects: bool
    category: str  # "READ", "WRITE", "EXECUTE", "SEARCH", "ANALYZE", "NETWORK"


def _metadata_from_spec(spec: Any) -> ToolMetadata:
    capabilities = spec.capabilities
    reads = bool(
        capabilities
        & {
            Capability.READ,
            Capability.VCS_READ,
        }
    )
    writes = bool(
        capabilities
        & {
            Capability.WRITE,
            Capability.VCS_WRITE,
        }
    )
    return ToolMetadata(
        cost=spec.cost,
        reads_disk=reads,
        writes_disk=writes,
        modifies_workspace=writes,
        cacheable=spec.cacheable,
        side_effects=spec.side_effects,
        category=spec.category,
    )


TOOL_METADATA: Dict[str, ToolMetadata] = {
    spec.name: _metadata_from_spec(spec) for spec in BUILTIN_SKILL_SPECS
}
# Custo por `action` de `file_writer`, usado por `estimate_step_cost` para
# refinar o valor padrÃ£o (pior caso) de TOOL_METADATA["file_writer"].cost.
_FILE_WRITER_ACTION_COST: Dict[str, int] = {
    "patch": 3,
    "ast_patch": 4,
    "append": 3,
    "delete_lines": 3,
    "write": 8,
}

# Metadado neutro/conservador para ferramentas ainda nÃ£o catalogadas acima.
# Tratado como o pior caso (lÃª e escreve disco, tem efeitos colaterais, nÃ£o
# Ã© cacheable) para nunca subestimar o risco de uma ferramenta desconhecida.
_DEFAULT_UNKNOWN_TOOL_METADATA = ToolMetadata(
    cost=5, reads_disk=True, writes_disk=True, modifies_workspace=True,
    cacheable=False, side_effects=True, category="EXECUTE",
)


def build_metadata_dict(registry: Any = None) -> Dict[str, ToolMetadata]:
    """Retorna o dicionÃ¡rio de ToolMetadata dinÃ¢mico a partir de um ToolRegistry.

    Se registry for None ou nÃ£o possuir metadata_dict(), retorna TOOL_METADATA estÃ¡tico.
    """
    if registry is not None and hasattr(registry, "metadata_dict") and callable(registry.metadata_dict):
        dynamic = registry.metadata_dict()
        if dynamic:
            return cast(Dict[str, ToolMetadata], dynamic)
    return dict(TOOL_METADATA)


def get_tool_metadata(tool: str) -> ToolMetadata:

    """Retorna o `ToolMetadata` de `tool`.

    Se a ferramenta nÃ£o estiver catalogada em `TOOL_METADATA` (ex.: uma
    skill nova ainda nÃ£o registrada aqui), retorna um metadado neutro e
    conservador em vez de lanÃ§ar `KeyError`, para que `PlanValidator` e
    `PlanOptimizer` continuem funcionando com seguranÃ§a mesmo diante de
    ferramentas desconhecidas.
    """
    return TOOL_METADATA.get(tool, _DEFAULT_UNKNOWN_TOOL_METADATA)


def estimate_step_cost(tool: str, args: Dict[str, Any]) -> int:
    """Estima o custo real de um passo especÃ­fico do plano, refinando o
    valor estÃ¡tico de `TOOL_METADATA` quando os `args` do passo permitem
    uma estimativa mais precisa.

    Regras de refinamento:
        - `file_reader` com `start_line`/`end_line` presentes -> leitura
          parcial (custo 2). Caso contrÃ¡rio -> leitura do arquivo inteiro
          (usa o custo padrÃ£o da ferramenta, 4).
        - `file_writer` -> usa o custo especÃ­fico da `action` informada em
          `args` (ver `_FILE_WRITER_ACTION_COST`); se a `action` nÃ£o for
          reconhecida, cai para o custo padrÃ£o (pior caso) da ferramenta.

    Qualquer outra ferramenta usa diretamente `TOOL_METADATA[tool].cost`.
    """
    args = args if isinstance(args, dict) else {}

    if tool == "file_reader":
        if "start_line" in args and "end_line" in args:
            return 2  # leitura parcial
        return get_tool_metadata(tool).cost  # leitura do arquivo inteiro

    if tool == "file_writer":
        action = args.get("action", "write")
        return _FILE_WRITER_ACTION_COST.get(action, get_tool_metadata(tool).cost)

    return get_tool_metadata(tool).cost
