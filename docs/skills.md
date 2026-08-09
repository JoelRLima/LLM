# Skills

Skills são adaptadores da borda do sistema. Elas validam argumentos, convertem
o pedido para um caso de uso e normalizam o resultado. Regras de negócio novas
devem permanecer nos domínios correspondentes.

## Registro canônico

`agent/skills/catalog.py` contém um `SkillSpec` por skill embutida, com:

- módulo, classe e nome externo;
- argumentos de construção;
- capacidades requeridas;
- custo, categoria, cache, idempotência e timeout.

`SkillRegistry` importa, instancia e rejeita nome duplicado ou divergência entre
o nome da implementação e o descritor. `load_all_skills()` compõe o registro e
permite injetar `Orchestrator`, `ModelGateway`, configuração, `ApprovalPort`,
raiz do projeto e scratch externo ao workspace.

`agent/planning/tool_metadata.py` deriva a visão exigida pelo planejador legado.
Não cadastre custo ou efeito em um segundo mapa.

## Capacidades e personas

Capacidades atuais:

- `read`, `write`, `process`, `network` e `memory`;
- `analyze`;
- `vcs_read` e `vcs_write`;
- `package_install`.

`agent/skills/policy.py` define quais capacidades cada persona recebe. Uma skill
é autorizada somente quando todas as suas capacidades estão no conjunto
permitido. Skills desconhecidas e capacidades não concedidas são negadas.

Essa política é conservadora: por exemplo, `shell` declara escrita mesmo que a
allowlist atual seja de inspeção/validação, pois validadores podem produzir
caches no workspace.

## Contrato da `BaseSkill`

Toda skill implementa:

```python
class MinhaSkill(BaseSkill):
    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    def get_schema(self) -> dict: ...

    def execute(self, args: dict) -> dict: ...
```

O resultado deve seguir:

```python
{
    "ok": bool,
    "done": bool,
    "data": object,
    "error": str | None,
    "message": str,
}
```

`normalize_tool_result()` protege consumidores contra retorno antigo
malformado, mas não elimina a responsabilidade da skill de cumprir o contrato.

## Skills embutidas

| Nome | Responsabilidade | Capacidades principais |
| :--- | :--- | :--- |
| `calculator` | expressões matemáticas por AST seguro | analyze |
| `code_analyzer` | análise Python AST ou textual explícita | read, analyze |
| `code_task` | workflows de engenharia de código | read, write, process, analyze |
| `directory_lister` | listagem restrita à raiz | read |
| `echo` | infraestrutura/teste | nenhuma |
| `file_reader` | leitura paginada e integração com workspace legado | read |
| `file_writer` | escrita no workspace legado com confirmação | read, write |
| `git_reader` | `status`, `log` e `diff` somente leitura | read, process, vcs_read |
| `grep` | busca textual restrita à raiz | read |
| `python_executor` | execução Python em workspace efêmero com defesas | process |
| `session_memory` | leitura/escrita de achados da memória | memory |
| `shell` | comandos allowlisted de inspeção e validação | read, write, process, vcs_read |
| `summarize` | resumo técnico por modelo | analyze |
| `web_search` | busca web | network |

O alias histórico `git` ainda é aceito onde o planejador legado exige
compatibilidade; o nome canônico é `git_reader`.

## `code_task`

A skill recebe `ModelGateway` e configuração por injeção, sem referência ao
`Orchestrator`. Ações:

- `analyze`: arquivo ou repositório, sem modelo;
- `review`: somente leitura, sem modelo;
- `generate`, `modify` e `refactor`: proposta de `ChangeSet`, aplicação e
  validação;
- `repair`: mesmo pipeline com tentativas limitadas;
- `template`: cria um `TaskGraph` determinístico para operações conhecidas;
- `multitask`: executa um `TaskGraph` validado.

Exemplo:

```json
{
  "action": "modify",
  "objective": "Adicionar validação sem mudar a API pública",
  "targets": ["service.py"],
  "include_tests": true
}
```

Sem gateway injetado, ações que exigem modelo falham explicitamente; análise e
review continuam disponíveis. Propostas de baixa confiança retornam `blocked`
sem escrever. Na aplicação standalone, a autorização vem do `ApprovalPort`:
chat pode perguntar, headless bloqueia e `run --yes` aprova somente aquela
execução. `auto_confirm: true` é compatibilidade para construção direta e
legada da skill. O campo externo `ok` só é verdadeiro para `succeeded`;
`unverified` permanece visível em `data.status` e nunca é promovido a sucesso.

## Segurança das skills existentes

- resolução de caminho compartilhada usa `safe_path.resolve_safe_path()`;
- varreduras de `grep` e `code_analyzer` resolvem cada arquivo descoberto e
  descartam symlinks que apontem para fora da raiz;
- `file_writer` valida a requisição e delega backup, diff, confirmação e
  escrita a `file_writer_runtime.py`;
- `shell` usa `shlex.split`, `shell=False`, timeout, allowlist e validação dos
  argumentos que representam paths; `ruff` consulta `ApprovalPort` antes da
  execução;
- `process_paths.py`, `process_safety.py` e `process_environment.py` isolam,
  respectivamente, paths, efeitos/allowlist e sanitização do ambiente usados
  por `shell` e `git_reader`;
- `python_executor` coordena quatro responsabilidades isoladas:
  `python_source_analysis.py` extrai propriedades sintáticas,
  `python_security_analysis.py` detecta construções inseguras,
  `python_sandbox_policy.py` decide a permissão e
  `python_sandbox_runtime.py` executa o subprocesso efêmero;
- `git_reader` não faz commit, checkout, push ou escrita;
- `session_memory` só reporta sucesso depois do commit SQLite;
- instalação de pacotes não é concedida às personas atuais.

Essas defesas não equivalem a uma sandbox de sistema operacional para código
hostil. Em particular, um validator como `pytest` executa código do próprio
projeto; confinamento de argumentos impede escapes diretos, mas não transforma
o processo em isolamento forte. A capability `process` deve ser tratada como
autoridade relevante.

### Closure do Marco 1: ShellSkill reduzida

Na closure atual, `shell` e um **restricted validation/read-only command
runner**. A superficie model-actionable exata e `ruff check`, `git status`,
`git log`, `git diff` e `tree` quando o executavel existe. `pytest`, `mypy` e
os aliases `echo`, `type`, `dir` e `ls` nao fazem parte da allowlist.

Ruff e executado com `--isolated --no-cache --no-fix`; configuracao explicita,
mutacao e `tree -o` sao rejeitados. Git recebe overrides para desabilitar pager,
fsmonitor, untracked cache, diff externo, textconv e verificacao de assinaturas;
flags e formatos de assinatura sao rejeitados. O nome allowlisted e resolvido
para um executavel absoluto/canonico fora do workspace; se so houver um
`ruff` dentro do workspace, ele fica indisponivel. O runner usa `shell=False`,
ambiente construido por allowlist e limites independentes de stdout/stderr.
Isso nao fornece sandbox de filesystem, rede, TOCTOU ou preempcao universal;
validacao de path nao limita efeitos transitivos de um programa.

## Como adicionar uma skill

1. Implemente `BaseSkill` em um módulo pequeno.
2. Delegue lógica de domínio a um serviço separado.
3. Adicione um único `SkillSpec` ao catálogo.
4. Declare todas as capacidades e efeitos de forma conservadora.
5. Injete dependências pela factory; não importe o `Orchestrator` no domínio.
6. Teste construção, schema, política, sucesso e falha.
7. Atualize esta tabela e o guia do domínio.

Testes principais: `tests/unit/skills/test_skill_registry.py`, `tests/unit/llm/test_router.py`,
`tests/unit/skills/test_shell.py`, `tests/unit/skills/test_python_executor.py` e
`tests/unit/code/test_coding_workflows.py`.
