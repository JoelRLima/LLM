from __future__ import annotations

import pytest

from agent.interaction.guards import (
    DirectOperationalRequestGuard,
    DirectOperationalTargetGuard,
    OperationalClassification,
    TargetProof,
)


@pytest.mark.parametrize(
    "text",
    [
        "refatore parser.py",
        "altere parser.py",
        "modifique parser.py",
        "aplique o patch em parser.py",
        "remova parser.py",
        "apague parser.py",
        "grave parser.py",
        "escreva parser.py",
        "commite as alterações",
        "run tests",
        "Run pytest -q",
        "Run tests with -q",
        "validate parser.py",
        "search the web for release notes",
        "download release.zip",
        "send https://example.com/item",
        "remember this_note",
        "install requests",
    ],
)
def test_closed_direct_effects_require_proven_target(text: str) -> None:
    analysis = DirectOperationalRequestGuard.analyze(text)
    assert analysis.classification is OperationalClassification.DIRECT
    assert DirectOperationalTargetGuard.classify(analysis) is TargetProof.PROVEN


@pytest.mark.parametrize(
    "text",
    [
        "Write a short summary",
        "Escreva uma explicação curta sobre AST",
        "Test my knowledge",
        "Envie um resumo curto",
        "Delete parser.py from the explanation",
        "Remove README.md from the list",
        "Write parser.py as an example",
        "Send parser.py as an example",
        "Install requests for this example",
    ],
)
def test_natural_language_effect_tails_never_prove_do_target(text: str) -> None:
    analysis = DirectOperationalRequestGuard.analyze(text)
    assert analysis.classification is OperationalClassification.DIRECT
    assert DirectOperationalTargetGuard.classify(analysis) is TargetProof.UNPROVEN


@pytest.mark.parametrize(
    "text",
    [
        "If tests pass, apply the patch",
        "Se os testes passarem, aplique o patch",
        "Apply the patch if tests pass",
        "Aplique o patch se os testes passarem",
        'Explain "delete parser.py"',
        "do that",
    ],
)
def test_hypothetical_quoted_and_contextual_effects_fail_closed(text: str) -> None:
    assert DirectOperationalRequestGuard.classify(text) in {
        OperationalClassification.HYPOTHETICAL,
        OperationalClassification.UNKNOWN,
        OperationalClassification.CONTEXTUAL,
    }
