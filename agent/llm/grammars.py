"""
agent/grammars.py

Infraestrutura de suporte a gramáticas GBNF (GGML BNF) para forçar o LLM
a gerar JSON estruturalmente válido, eliminando falhas de parsing.

A gramática usada em cada requisição é escolhida automaticamente com base
no `step_type` do passo do agente, mas pode ser desabilitada (grammar=None)
ou sobrescrita (grammar=<string>) pelo chamador.

As gramáticas definidas aqui são placeholders simples: garantem apenas a
forma estrutural básica do JSON esperado. Validação semântica (nomes de
ferramentas válidos, tipos de argumentos, etc.) continua sendo
responsabilidade do PlanValidator.
"""
from typing import Any, Dict, Mapping, Optional

from agent.runtime import config as _config

# ----------------------------------------------------------------------
# Sentinela para seleção automática de gramática
# ----------------------------------------------------------------------


class AutoGrammar:
    """Sentinela que indica que a gramática deve ser escolhida
    automaticamente com base no `step_type` do passo atual."""

    pass


AUTO_GRAMMAR = AutoGrammar()


# ----------------------------------------------------------------------
# Blocos GBNF compartilhados (placeholders simples)
# ----------------------------------------------------------------------

_COMMON_RULES = r"""
ws     ::= [ \t\n]*
string ::= "\"" ([^"\\] | "\\" .)* "\""
number ::= "-"? [0-9]+ ("." [0-9]+)?
boolean ::= "true" | "false"
value  ::= object | array | string | number | boolean | "null"
object ::= "{" ws (member ("," ws member)*)? ws "}"
member ::= string ws ":" ws value
array  ::= "[" ws (value ("," ws value)*)? ws "]"
"""

# ----------------------------------------------------------------------
# Gramáticas por formato de resposta
# ----------------------------------------------------------------------

PLAN_GRAMMAR = (
    r"""
root      ::= plan-response | continue-after-plan-response | direct-response
plan-response ::= "{" ws "\"action\"" ws ":" ws "\"use_tools\"" ws "," ws "\"plan\"" ws ":" ws "[" ws (plan-item ("," ws plan-item)*)? ws "]" ws "}"
continue-after-plan-response ::= "{" ws "\"action\"" ws ":" ws "\"continue_after_plan\"" ws "," ws "\"plan\"" ws ":" ws "[" ws (plan-item ("," ws plan-item)*)? ws "]" ws "}"
direct-response ::= "{" ws "\"action\"" ws ":" ws "\"direct_response\"" ws "," ws "\"answer\"" ws ":" ws string ws "}"
plan-item ::= tool-step | deferred-condition
tool-step ::= "{" ws "\"tool\"" ws ":" ws string ws "," ws "\"args\"" ws ":" ws object (ws "," ws "\"bindings\"" ws ":" ws bindings-object)? ws "}"
deferred-condition ::= "{" ws "\"kind\"" ws ":" ws "\"deferred_condition\"" ws "," ws "\"observation_ref\"" ws ":" ws integer ws "," ws "\"predicate\"" ws ":" ws equals-predicate ws "," ws "\"on_true\"" ws ":" ws tool-step ws "," ws "\"on_false\"" ws ":" ws write-waiver ws "}"
equals-predicate ::= "{" ws "\"op\"" ws ":" ws "\"equals\"" ws "," ws "\"value\"" ws ":" ws string ws "}"
write-waiver ::= "{" ws "\"waive_effect\"" ws ":" ws "\"write\"" ws "}"
integer ::= [1-9] [0-9]*
bindings-object ::= "{" ws (binding-member ("," ws binding-member)*)? ws "}"
binding-member ::= string ws ":" ws binding-spec
binding-spec ::= "{" ws "\"from_step\"" ws ":" ws integer ws "," ws "\"path\"" ws ":" ws path-array ws "}"
path-array ::= "[" ws (path-segment ("," ws path-segment)*)? ws "]"
path-segment ::= string | integer
"""
    + _COMMON_RULES
)

CONTINUATION_PLAN_GRAMMAR = (
    r"""
root      ::= execute-response | complete-response | complete-without-effect-response | blocked-response
execute-response ::= "{" ws "\"action\"" ws ":" ws "\"execute\"" ws "," ws "\"plan\"" ws ":" ws "[" ws (plan-item ("," ws plan-item)*)? ws "]" ws "}"
complete-without-effect-response ::= "{" ws "\"action\"" ws ":" ws "\"complete_without_effect\"" ws "," ws "\"observation_index\"" ws ":" ws integer ws "}"
complete-response ::= "{" ws "\"action\"" ws ":" ws "\"complete\"" ws "," ws "\"reason\"" ws ":" ws string ws "}"
blocked-response ::= "{" ws "\"action\"" ws ":" ws "\"blocked\"" ws "," ws "\"reason\"" ws ":" ws string ws "}"
plan-item ::= "{" ws "\"tool\"" ws ":" ws string ws "," ws "\"args\"" ws ":" ws object (ws "," ws "\"bindings\"" ws ":" ws bindings-object)? ws "}"
bindings-object ::= "{" ws (binding-member ("," ws binding-member)*)? ws "}"
binding-member ::= string ws ":" ws binding-spec
binding-spec ::= "{" ws "\"from_step\"" ws ":" ws integer ws "," ws "\"path\"" ws ":" ws path-array ws "}"
path-array ::= "[" ws (path-segment ("," ws path-segment)*)? ws "]"
path-segment ::= string | integer
integer ::= [1-9] [0-9]*
"""
    + _COMMON_RULES
)

MACRO_PLAN_GRAMMAR = (
    r"""
root       ::= "{" ws "\"steps\"" ws ":" ws "[" ws (step-item ("," ws step-item)*)? ws "]" ws "}"
step-item  ::= "{" ws "\"id\"" ws ":" ws string ws "," ws "\"title\"" ws ":" ws string ws "," ws "\"goal\"" ws ":" ws string ws "," ws "\"priority\"" ws ":" ws string (ws "," ws "\"depends_on\"" ws ":" ws string-array)? (ws "," ws "\"estimated_tools\"" ws ":" ws string-array)? ws "}"
string-array ::= "[" ws (string ("," ws string)*)? ws "]"
"""
    + _COMMON_RULES
)

_TOOL_DECISION_PRODUCTIONS = r"""
tool-decision ::= "{" ws "\"action\"" ws ":" ws "\"tool\"" ws "," ws "\"tool\"" ws ":" ws string ws "," ws "\"args\"" ws ":" ws object (ws "," ws "\"bindings\"" ws ":" ws bindings-object)? ws "}"
bindings-object ::= "{" ws (binding-member ("," ws binding-member)*)? ws "}"
binding-member ::= string ws ":" ws binding-spec
binding-spec ::= "{" ws "\"from_step\"" ws ":" ws integer ws "," ws "\"path\"" ws ":" ws path-array ws "}"
path-array ::= "[" ws (path-segment ("," ws path-segment)*)? ws "]"
path-segment ::= string | integer
integer ::= [1-9] [0-9]*
"""

_FINAL_DECISION_PRODUCTION = r"""
final-decision ::= "{" ws "\"action\"" ws ":" ws "\"final\"" ws "," ws "\"answer\"" ws ":" ws string ws "}"
"""

TOOL_DECISION_GRAMMAR = (
    r"""
root ::= tool-decision | final-decision
"""
    + _TOOL_DECISION_PRODUCTIONS
    + _FINAL_DECISION_PRODUCTION
    + _COMMON_RULES
)

REPLAN_GRAMMAR = (
    r"""
root ::= tool-decision
"""
    + _TOOL_DECISION_PRODUCTIONS
    + _COMMON_RULES
)

FINAL_GRAMMAR = (
    r"""
root ::= "{" ws "\"answer\"" ws ":" ws string ws "}"
"""
    + _COMMON_RULES
)

SUMMARIZE_GRAMMAR = (
    r"""
root ::= "{" ws "\"summary\"" ws ":" ws string ws "}"
"""
    + _COMMON_RULES
)


# ----------------------------------------------------------------------
# Mapeamento step_type -> gramática
# ----------------------------------------------------------------------

GRAMMARS: Dict[str, str] = {
    "plan": PLAN_GRAMMAR,
    "continuation_plan": CONTINUATION_PLAN_GRAMMAR,
    "macro_plan": MACRO_PLAN_GRAMMAR,
    "tool_decision": TOOL_DECISION_GRAMMAR,
    "final": FINAL_GRAMMAR,
    "summarize": SUMMARIZE_GRAMMAR,
    "replan": REPLAN_GRAMMAR,
}


def get_grammar(
    step_type: str,
    config: Mapping[str, Any] | None = None,
) -> Optional[str]:
    """
    Retorna a gramática GBNF correspondente ao `step_type`, ou None se
    não houver gramática mapeada ou se o suporte a GBNF estiver
    desabilitado via `ENABLE_GBNF` em config.py.

    Args:
        step_type: tipo do passo (plan, macro_plan, tool_decision, final,
            summarize, replan, etc.).
        config: configuração efetiva da sessão/profile. Quando omitida,
            preserva a compatibilidade dos callers legados com DEFAULT_CONFIG.

    Returns:
        A string da gramática GBNF, ou None.
    """
    effective_config = _config.DEFAULT_CONFIG if config is None else config
    if not effective_config.get("ENABLE_GBNF", True):
        return None
    return GRAMMARS.get(step_type)
