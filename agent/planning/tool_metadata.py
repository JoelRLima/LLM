"""
Visão legada de custo e características das ferramentas (skills), derivada do
catálogo canônico disponível para o agente.

Usado por `PlanValidator` e `PlanOptimizer` para tomar decisões de custo,
reordenação e deduplicação SEM precisar conhecer os nomes das ferramentas
individualmente — a lógica desses módulos é escrita inteiramente em termos
de metadados (cost, category, side_effects, cacheable...), nunca de nomes
de ferramentas hardcoded.

Referência de custos usada para calibrar os valores abaixo (ordem de
grandeza relativa, não um tempo absoluto):

    grep, directory_lister, echo             -> 1
    code_analyzer, file_reader (parcial)      -> 2
    patch (file_writer, action='patch')       -> 3
    file_reader (arquivo inteiro), ast_patch  -> 4
    web_search, summarize                     -> 5
    python_executor                           -> 6
    shell                                     -> 7
    write (file_writer, action='write')       -> 8

Observação sobre granularidade — `file_writer` e `file_reader`:
    `ToolMetadata.cost` é um único inteiro por ferramenta (assim como
    definido no schema solicitado), mas o custo real de `file_writer`
    varia conforme a `action` usada (um 'patch' é bem mais barato que um
    'write' completo), e o custo de `file_reader` varia conforme o
    trecho lido (parcial vs. arquivo inteiro). Como o dataclass não tem um
    campo por ação/argumento, `TOOL_METADATA[...].cost` guarda o valor de
    pior caso para cada ferramenta (write=8 para file_writer; leitura de
    arquivo inteiro=4 para file_reader), e a função `estimate_step_cost()`
    abaixo refina esse valor a partir dos `args` de um passo concreto
    quando isso é possível. `PlanOptimizer` usa `estimate_step_cost` para
    calcular os custos "antes/depois" reportados em `OptimizationReport`.

Ferramentas sem custo de referência explícito no pedido original (`git`,
`calculator`, `session_memory`) recebem valores conservadores por analogia
a ferramentas semelhantes — ajuste livremente se o custo real observado em
produção divergir.
"""
from dataclasses import dataclass
from typing import Any, Dict

from agent.skills.catalog import BUILTIN_SKILL_SPECS
from agent.skills.descriptor import SkillCapability


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
            SkillCapability.READ,
            SkillCapability.VCS_READ,
        }
    )
    writes = bool(
        capabilities
        & {
            SkillCapability.WRITE,
            SkillCapability.VCS_WRITE,
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
# Alias temporário para planos antigos; o nome público real da skill é
# `git_reader` e novos planos recebem esse nome pelo catálogo.
TOOL_METADATA["git"] = TOOL_METADATA["git_reader"]


# Custo por `action` de `file_writer`, usado por `estimate_step_cost` para
# refinar o valor padrão (pior caso) de TOOL_METADATA["file_writer"].cost.
_FILE_WRITER_ACTION_COST: Dict[str, int] = {
    "patch": 3,
    "ast_patch": 4,
    "append": 3,
    "delete_lines": 3,
    "write": 8,
}

# Metadado neutro/conservador para ferramentas ainda não catalogadas acima.
# Tratado como o pior caso (lê e escreve disco, tem efeitos colaterais, não
# é cacheable) para nunca subestimar o risco de uma ferramenta desconhecida.
_DEFAULT_UNKNOWN_TOOL_METADATA = ToolMetadata(
    cost=5, reads_disk=True, writes_disk=True, modifies_workspace=True,
    cacheable=False, side_effects=True, category="EXECUTE",
)


def get_tool_metadata(tool: str) -> ToolMetadata:
    """Retorna o `ToolMetadata` de `tool`.

    Se a ferramenta não estiver catalogada em `TOOL_METADATA` (ex.: uma
    skill nova ainda não registrada aqui), retorna um metadado neutro e
    conservador em vez de lançar `KeyError`, para que `PlanValidator` e
    `PlanOptimizer` continuem funcionando com segurança mesmo diante de
    ferramentas desconhecidas.
    """
    return TOOL_METADATA.get(tool, _DEFAULT_UNKNOWN_TOOL_METADATA)


def estimate_step_cost(tool: str, args: Dict[str, Any]) -> int:
    """Estima o custo real de um passo específico do plano, refinando o
    valor estático de `TOOL_METADATA` quando os `args` do passo permitem
    uma estimativa mais precisa.

    Regras de refinamento:
        - `file_reader` com `start_line`/`end_line` presentes -> leitura
          parcial (custo 2). Caso contrário -> leitura do arquivo inteiro
          (usa o custo padrão da ferramenta, 4).
        - `file_writer` -> usa o custo específico da `action` informada em
          `args` (ver `_FILE_WRITER_ACTION_COST`); se a `action` não for
          reconhecida, cai para o custo padrão (pior caso) da ferramenta.

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
