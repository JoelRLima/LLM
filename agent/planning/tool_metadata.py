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

Ferramentas sem custo de referência explícito no pedido original (`git_reader`,
`calculator`, `session_memory`) recebem valores conservadores por analogia
a ferramentas semelhantes — ajuste livremente se o custo real observado em
produção divergir.
"""
from agent.operation.planning_metadata import (
    TOOL_METADATA,
    ToolMetadata,
    build_metadata_dict,
    estimate_step_cost,
    get_tool_metadata,
)

__all__ = [
    "TOOL_METADATA",
    "ToolMetadata",
    "build_metadata_dict",
    "estimate_step_cost",
    "get_tool_metadata",
]
