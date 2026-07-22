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
from typing import Dict, Optional

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
root      ::= "{" ws "\"plan\"" ws ":" ws "[" ws (plan-item ("," ws plan-item)*)? ws "]" ws "}"
plan-item ::= "{" ws "\"tool\"" ws ":" ws string ws "," ws "\"args\"" ws ":" ws object ws "}"
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

TOOL_DECISION_GRAMMAR = (
    r"""
root ::= "{" ws "\"tool\"" ws ":" ws string ws "," ws "\"args\"" ws ":" ws object ws "}"
"""
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
    "macro_plan": MACRO_PLAN_GRAMMAR,
    "tool_decision": TOOL_DECISION_GRAMMAR,
    "final": FINAL_GRAMMAR,
    "summarize": SUMMARIZE_GRAMMAR,
    "replan": TOOL_DECISION_GRAMMAR,
}


def get_grammar(step_type: str) -> Optional[str]:
    """
    Retorna a gramática GBNF correspondente ao `step_type`, ou None se
    não houver gramática mapeada ou se o suporte a GBNF estiver
    desabilitado via `ENABLE_GBNF` em config.py.

    Args:
        step_type: tipo do passo (plan, macro_plan, tool_decision, final,
            summarize, replan, etc.).

    Returns:
        A string da gramática GBNF, ou None.
    """
    if not _config.DEFAULT_CONFIG.get("ENABLE_GBNF", True):
        return None
    return GRAMMARS.get(step_type)
