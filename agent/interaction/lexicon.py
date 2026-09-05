"""Closed W12 lexical tables.  These values are intentionally finite."""

from __future__ import annotations

WRITE_PT = (
    "refatore", "altere", "modifique", "aplique", "remova", "apague",
    "grave", "escreva", "commite", "faça commit", "faca commit",
)
WRITE_EN = ("refactor", "modify", "change", "apply", "remove", "delete", "write", "commit")
PROCESS_PT = ("execute", "rode", "teste", "valide")
PROCESS_EN = ("run", "execute", "test", "validate")
NETWORK_PT = ("pesquise na web", "busque na web", "baixe", "envie", "poste")
NETWORK_EN = ("search the web", "browse the web", "download", "send", "post")
MEMORY_PT = (
    "lembre", "guarde na memória", "guarde na memoria", "esqueça", "esqueca",
    "apague da memória", "apague da memoria",
)
MEMORY_EN = ("remember", "store in memory", "forget", "delete from memory")
PACKAGE_PT = ("instale",)
PACKAGE_EN = ("install",)

EFFECT_LEXICON = {
    "WRITE": tuple(sorted(WRITE_PT + WRITE_EN, key=len, reverse=True)),
    "PROCESS": tuple(sorted(PROCESS_PT + PROCESS_EN, key=len, reverse=True)),
    "NETWORK": tuple(sorted(NETWORK_PT + NETWORK_EN, key=len, reverse=True)),
    "MEMORY": tuple(sorted(MEMORY_PT + MEMORY_EN, key=len, reverse=True)),
    "PACKAGE_INSTALL": tuple(sorted(PACKAGE_PT + PACKAGE_EN, key=len, reverse=True)),
}

ALL_EFFECT_LEXEMES = tuple(
    sorted((item for values in EFFECT_LEXICON.values() for item in values), key=len, reverse=True)
)

REQUEST_PREFIXES = tuple(
    sorted(
        (
            "por favor,", "por favor", "pode", "poderia", "quero que você", "quero que voce",
            "please,", "please", "can you", "could you", "i want you to",
        ),
        key=len,
        reverse=True,
    )
)

NEGATION_PREFIXES = ("não", "nao", "do not", "don't", "don’t", "dont", "never")

NEGATIVE_INFINITIVE_FORMS: dict[str, dict[str, tuple[str, ...]]] = {
    "WRITE": {
        "TARGET_SCOPED": (
            "sem alterar", "sem modificar", "sem refatorar", "sem aplicar", "sem remover",
            "sem apagar", "sem gravar", "sem escrever", "without changing", "without modifying",
            "without refactoring", "without applying", "without removing", "without deleting",
            "without writing",
        ),
    },
    "PROCESS": {
        "TARGET_SCOPED": (
            "sem executar", "sem rodar", "sem testar", "sem validar", "without running",
            "without executing", "without testing", "without validating",
        ),
    },
    "NETWORK": {
        "FAMILY_ALL": (
            "sem pesquisar na web", "sem buscar na web", "without searching the web",
            "without browsing the web",
        ),
        "TARGET_SCOPED": (
            "sem baixar", "sem enviar", "sem postar", "without downloading", "without sending",
            "without posting",
        ),
    },
    "PACKAGE_INSTALL": {
        "TARGET_SCOPED": ("sem instalar", "without installing"),
    },
}

GLOBAL_RESTRICTIONS = (
    "não altere nada", "nao altere nada", "não faça nenhuma alteração", "nao faca nenhuma alteracao",
    "não faça nada", "nao faca nada", "não execute nada", "nao execute nada",
    "não faça nenhuma operação", "nao faca nenhuma operacao", "do not change anything",
    "do not modify anything", "make no changes", "do nothing", "do not do anything",
    "do not execute anything", "perform no operation",
)

GLOBAL_FAMILY_CORES = {
    "NETWORK": (
        "não pesquise na web", "nao pesquise na web", "não busque na web", "nao busque na web",
        "não use a rede", "nao use a rede", "não use a internet", "nao use a internet",
        "do not search the web", "don't search the web", "don’t search the web", "dont search the web",
        "never search the web", "do not browse the web", "don't browse the web", "don’t browse the web",
        "dont browse the web", "never browse the web", "do not use the network", "don't use the network",
        "don’t use the network", "dont use the network", "never use the network", "do not use the internet",
        "don't use the internet", "don’t use the internet", "dont use the internet", "never use the internet",
    ),
    "PROCESS": (
        "não rode testes", "nao rode testes", "não rode os testes", "nao rode os testes",
        "não execute testes", "nao execute testes", "não execute os testes", "nao execute os testes",
        "do not run tests", "don't run tests", "don’t run tests", "dont run tests", "never run tests",
        "do not run the tests", "don't run the tests", "don’t run the tests", "dont run the tests",
        "never run the tests", "do not execute tests", "don't execute tests", "don’t execute tests",
        "dont execute tests",
    ),
}

TARGET_SCOPED_NEGATIVE_EFFECTS = {
    "WRITE": (
        "refatore", "altere", "modifique", "aplique", "remova", "apague", "grave", "escreva",
        "commite", "faça commit", "faca commit", "mexa", "toque", "refactor", "modify", "change",
        "apply", "remove", "delete", "write", "commit", "touch",
    ),
    "PROCESS": PROCESS_PT + PROCESS_EN,
    "NETWORK": ("baixe", "envie", "poste", "download", "send", "post"),
    "MEMORY": MEMORY_PT + MEMORY_EN,
    "PACKAGE_INSTALL": PACKAGE_PT + PACKAGE_EN,
}

GENERIC_TARGET_NOUNS = frozenset(
    {
        "file", "files", "arquivo", "arquivos", "module", "modules", "módulo", "módulos",
        "modulo", "modulos", "test", "tests", "teste", "testes", "patch", "patches", "repo",
        "repository", "repositório", "repositorio", "project", "projeto", "code", "código", "codigo",
        "branch", "ramo", "memory", "memória", "memoria", "package", "pacote", "network", "rede",
        "internet", "web",
    }
)
DEICTIC_TARGETS = frozenset(
    {"isso", "isto", "esse", "essa", "this", "that", "it", "aquilo", "the previous one"}
)
BROAD_TARGET_QUANTIFIERS = frozenset(
    {"tudo", "todos", "todas", "qualquer", "nenhum", "nenhuma", "all", "every", "everything", "any", "no"}
)

READ_LEXEMES = (
    "análise",
    "analise", "analise", "revise", "inspecione", "leia", "compare", "explique", "resuma",
    "o que é", "o que e", "analyze", "review", "inspect", "read", "compare", "explain", "summarize", "what is",
)
PLAN_LEXEMES = (
    "proponha uma correção para", "proponha uma correcao para",
    "planeje", "faça um plano para", "faca um plano para", "monte um plano para", "elabore um plano para",
    "proponha um plano para", "como você refatoraria", "como voce refatoraria", "plan", "make a plan for",
    "create a plan for", "outline a plan for", "propose a plan for", "how would you refactor",
)

MIXED_INTENT_MARKERS = (
    "e então", "e entao", ", então", ", entao", ", depois", "e", "então", "entao", "depois", ",",
    "and then", ", then", "and", "then", ",",
)
LOCAL_CONFLICT_SEPARATORS = (
    "and then", "but", "and", ",",
    "e então", "e entao", "mas", "porém", "porem", "e",
)
PROCESS_OBJECT_HEADS = frozenset(
    {
        "os testes", "testes", "pytest", "ruff", "mypy", "o script", "script", "o comando", "comando",
        "the tests", "tests", "the script", "the command", "command",
    }
)

CONDITIONAL_PREFIXES = (
    "o que aconteceria se", "como seria se", "se", "caso", "what would happen if", "what if",
    "how would it work if", "if",
)

__all__ = [name for name in globals() if name.isupper()]
