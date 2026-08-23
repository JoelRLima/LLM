import pytest

from agent.planning.requested_effects import infer_requested_effects


@pytest.mark.parametrize(
    "objective",
    [
        "Não altere nenhum arquivo.",
        "Nao altere nenhum arquivo.",
        "Não modifique nada.",
        "Nao modifique nada.",
        "Não escreva nada.",
        "Nao escreva nada.",
        "Não crie arquivos.",
        "Nao crie arquivos.",
        "Não remova arquivos.",
        "Nao remova arquivos.",
        "Sem alterar arquivos.",
        "Sem modificar arquivos.",
        "Sem escrever arquivos.",
        "Do not alter any files.",
        "Do not modify any files.",
        "Do not change any files.",
        "Do not write any files.",
        "Do not create any files.",
        "Do not delete any files.",
        "Don't modify any files.",
        "Don't write any files.",
        "Without modifying files.",
        "Without changing files.",
    ],
)
def test_negative_mutation_language_is_effect_free(objective: str) -> None:
    assert infer_requested_effects(objective) == ()


@pytest.mark.parametrize(
    "objective",
    [
        "Altere controle.txt para modificado.",
        "Aplique a alteracao deterministica e valide.",
        "Proponha uma modificacao sem aplicar.",
        "Proponha uma modifica\u00c3\u00a7\u00c3\u00a3o sem aplicar.",
        "Modifique o arquivo.",
        "Write the file.",
        "Delete the temporary file.",
    ],
)
def test_positive_mutation_language_requests_write(objective: str) -> None:
    assert infer_requested_effects(objective) == ("write",)


def test_positive_conditional_write_survives_negative_else_branch() -> None:
    objective = (
        "Se X for verdadeiro, escreva Y; caso contrário, não altere nada."
    )
    assert infer_requested_effects(objective) == ("write",)


@pytest.mark.parametrize(
    "objective",
    [
        "Escreva exatamente o texto abaixo.",
        "Write exactly the text below.",
    ],
)
def test_direct_text_request_remains_effect_free(objective: str) -> None:
    assert infer_requested_effects(objective) == ()
