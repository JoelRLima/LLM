"""Stable W12 reason codes and bounded public explanations."""

from __future__ import annotations

INTERACTION_INPUT_REQUIRED = "INTERACTION_INPUT_REQUIRED"
INTERACTION_INPUT_TOO_LARGE = "INTERACTION_INPUT_TOO_LARGE"
INTERACTION_INPUT_INVALID = "INTERACTION_INPUT_INVALID"
INTERACTION_TRANSCRIPT_INVALID = "INTERACTION_TRANSCRIPT_INVALID"
INTERACTION_TASK_INTENT_REQUIRED = "INTERACTION_TASK_INTENT_REQUIRED"
INTERACTION_INTENT_AMBIGUOUS = "INTERACTION_INTENT_AMBIGUOUS"
INTERACTION_RESOLVER_UNAVAILABLE = "INTERACTION_RESOLVER_UNAVAILABLE"
INTERACTION_RESOLVER_INVALID = "INTERACTION_RESOLVER_INVALID"
INTERACTION_REQUEST_CONTRACT_MISMATCH = "INTERACTION_REQUEST_CONTRACT_MISMATCH"
INTERACTION_EVIDENCE_MISMATCH = "INTERACTION_EVIDENCE_MISMATCH"
INTERACTION_EFFECT_AMBIGUOUS = "INTERACTION_EFFECT_AMBIGUOUS"
INTERACTION_CONTEXT_GROUNDING_REQUIRED = "INTERACTION_CONTEXT_GROUNDING_REQUIRED"
INTERACTION_CONFLICT = "INTERACTION_CONFLICT"
INTERACTION_CONTINUATION_AMBIGUOUS = "INTERACTION_CONTINUATION_AMBIGUOUS"
INTERACTION_RESUME_OVERRIDE_FORBIDDEN = "INTERACTION_RESUME_OVERRIDE_FORBIDDEN"
INTERACTION_RESPONSE_UNAVAILABLE = "INTERACTION_RESPONSE_UNAVAILABLE"
INTERACTION_RESPONSE_CONTEXT_TOO_LARGE = "INTERACTION_RESPONSE_CONTEXT_TOO_LARGE"
INTERACTION_RESPONSE_FAILED = "INTERACTION_RESPONSE_FAILED"
INTERACTION_CANCELLED = "INTERACTION_CANCELLED"
INTERACTION_INTERNAL_FAILED = "INTERACTION_INTERNAL_FAILED"

_EXPLANATIONS = {
    INTERACTION_INPUT_REQUIRED: "Informe o que você quer que eu faça ou responda.",
    INTERACTION_INPUT_TOO_LARGE: "A solicitação é longa demais para este limite de interação. Reduza o conteúdo e tente novamente.",
    INTERACTION_INPUT_INVALID: "A solicitação de tarefa é inválida. Revise os prefixos ou o objetivo e tente novamente.",
    INTERACTION_TRANSCRIPT_INVALID: "O histórico da sessão está estruturalmente inválido. Recarregue um histórico válido ou limpe a sessão.",
    INTERACTION_TASK_INTENT_REQUIRED: "Você abriu uma tarefa, mas o objetivo não indica com segurança análise, plano ou execução. Especifique o que deseja fazer.",
    INTERACTION_INTENT_AMBIGUOUS: "Não consegui provar com segurança se este turno deve ser tratado como conversa, análise, plano ou execução. Especifique a intenção.",
    INTERACTION_RESOLVER_UNAVAILABLE: "Não consegui determinar com segurança a intenção desta solicitação. Reformule ou use /read, /plan, /do ou /continue dentro de uma tarefa.",
    INTERACTION_RESOLVER_INVALID: "A classificação da solicitação não pôde ser validada com segurança. Reformule ou use um controle explícito de tarefa.",
    INTERACTION_REQUEST_CONTRACT_MISMATCH: "Não consegui validar com segurança a classificação desta solicitação. Reformule ou use um controle explícito de tarefa.",
    INTERACTION_EVIDENCE_MISMATCH: "Não consegui confirmar no seu texto atual a evidência necessária para admitir essa ação.",
    INTERACTION_EFFECT_AMBIGUOUS: "Não está claro se você quer apenas discutir ou propor uma operação, ou realmente executá-la. Especifique qual das duas opções deseja.",
    INTERACTION_CONTEXT_GROUNDING_REQUIRED: "Descreva no texto atual o alvo exato da análise, do plano ou da operação.",
    INTERACTION_CONFLICT: "Seu pedido atual contém instruções conflitantes. Especifique qual instrução deve prevalecer.",
    INTERACTION_CONTINUATION_AMBIGUOUS: "Não está claro se você quer continuar esta conversa ou retomar a tarefa interrompida. Para retomar a tarefa, diga explicitamente que deseja retomar a tarefa anterior.",
    INTERACTION_RESUME_OVERRIDE_FORBIDDEN: "Uma retomada preserva a diretiva, o perfil e o objetivo salvos. Retome sem modificadores ou inicie uma nova tarefa com as novas condições.",
    INTERACTION_RESPONSE_UNAVAILABLE: "O modelo está indisponível para responder a esta interação.",
    INTERACTION_RESPONSE_CONTEXT_TOO_LARGE: "O histórico e a solicitação atuais não cabem juntos no contexto disponível. Reduza o histórico ou o conteúdo atual e tente novamente.",
    INTERACTION_RESPONSE_FAILED: "Não foi possível produzir uma resposta estável para esta interação.",
    INTERACTION_CANCELLED: "Interação cancelada pelo usuário.",
    INTERACTION_INTERNAL_FAILED: "A interação falhou internamente antes de produzir um resultado estável.",
}


def public_explanation(reason_code: str) -> str:
    return _EXPLANATIONS.get(reason_code, _EXPLANATIONS[INTERACTION_INTERNAL_FAILED])


class InteractionAdmissionError(ValueError):
    """A deterministic W12 admission failure with no raw implementation text."""

    def __init__(self, reason_code: str, *, resolution: object | None = None) -> None:
        self.reason_code = reason_code
        self.code = reason_code
        self.resolution = resolution
        super().__init__(reason_code)


class InteractionResolutionParseError(ValueError):
    """Strict parser failure for the resolver's eight-field response."""

    def __init__(self, detail: str = INTERACTION_RESOLVER_INVALID) -> None:
        self.reason_code = INTERACTION_RESOLVER_INVALID
        super().__init__(detail)


__all__ = [name for name in globals() if name.startswith("INTERACTION_")] + [
    "InteractionAdmissionError",
    "InteractionResolutionParseError",
    "public_explanation",
]
