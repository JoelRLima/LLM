# Tools, skills e superfície model-actionable

> **STATUS: CURRENT — PRIMARY HOME.** Este documento classifica o que existe no
> código, o que o modelo pode selecionar e o que é capability suportada.
> Authority e approval pertencem a [security.md](security.md); processo e stdio
> pertencem a [runtime.md](runtime.md).

## Camadas

`SkillRegistry` constrói builtins a partir de `agent/skills/catalog.py`.
`BuiltinToolAdapter` converte seus descritores para `ToolDescriptor`.
`ToolRegistry` agrega builtins e extensions. O planning recebe apenas uma
projeção elegível; `ToolInvocationGateway` aplica enforcement na execução.

```text
exists in code ≠ model-actionable ≠ supported product capability
```

Uma API low-level pode ser útil a administração/testes sem ser apresentada ao
modelo. Uma tool apresentada ainda pode ser negada por authority ou approval.

## Builtins CURRENT

| Tool | Função | Exposição model-actionable |
| :--- | :--- | :--- |
| `file_reader` | leitura UTF-8 confinada | sim, conforme persona |
| `directory_lister` | listagem confinada | sim, conforme persona |
| `grep` | busca textual confinada | sim, conforme persona |
| `code_analyzer` | análise AST/textual | sim, conforme persona |
| `code_task` | analyze/review/generate/modify/repair/refactor/template/multitask | sim; é o caminho suportado de mudança |
| `file_writer` | escrita direta legada/admin | **não**; excluído por `MODEL_ACTIONABLE_EXCLUDED` |
| `shell` | runner reduzido | sim somente quando a persona possui todas as capabilities |
| `git_reader` | histórico Git local | sim, conforme persona |
| `python_executor` | Python com policy própria | sim, conforme persona; não é sandbox forte |
| `session_memory` | memória da sessão/workspace | sim, conforme persona |
| `web_search` | busca DuckDuckGo | sim para a persona com network e com approval de efeito |
| `calculator`, `echo`, `summarize` | cálculo, infraestrutura e resumo | sim, conforme persona |

### Modify + validate

O caminho model-actionable do coder é:

```text
code_task → CodingApplicationService → ChangeSet → approval → commit
          → ProjectValidator → succeeded | unverified | rollback + failed
```

`file_writer` permanece registrado porque consumidores low-level e legados o
usam, mas não aparece nas views do planner. Um plano do modelo que tente
chamá-lo é filtrado/bloqueado e não deve mutar o workspace. Validação ausente
gera `unverified`, nunca sucesso artificial.

### Shell, Git e Ruff reduzidos

`ShellSkill` não é shell arbitrário. Usa `shell=False`, executável confiável
fora do workspace, ambiente allowlisted, timeout e streams limitados. A
superfície aceita somente:

- `git log` com formato fixo, sem patch/pathspec/remerge/helpers e com contador
  opcional limitado a 1–1000;
- `ruff check` isolado, sem cache e sem fix; flags de mutação, output, watch ou
  config explícita são rejeitadas;
- `tree` quando um executável confiável está disponível, sem output em arquivo.

`GitSkill` (`git_reader`) expõe somente `log`. `git status`, `git diff`, commit,
checkout, push, `pytest`, `mypy` e comandos arbitrários foram reduzidos away da
superfície model-actionable. Ruff é validation read-only na semântica do
produto, embora o descriptor de `shell` declare capabilities conservadoras e
o gateway solicite approval para processo/escrita potencial.

## Extensions externas

Uma tool stdio so entra no runtime apos a cadeia; por iniciar um processo
externo, seu manifest e descriptor exigem explicitamente `process`:

```text
catálogo global + configuração/grants do workspace
→ resolução ready
→ ApplicationExtensionBootstrap
→ binding e ToolRegistry congelado
→ task authority snapshot
→ planning context elegível
→ ToolInvocationGateway
→ StdioToolAdapter
```

Registro, descoberta, manifest válido e apresentação ao planner não concedem
authority. O `AgentApplication` sem `TaskAuthoritySnapshot` mantém extensions
invisíveis e não executáveis. A API programática suporta injetar esse snapshot;
a CLI padrão não o concede automaticamente.

O subcomando `llm-agent tools` permanece legado e administra apenas
`extensions/registry.json`; ele não configura o catálogo/workspace consumido
por `ApplicationExtensionBootstrap`. O workflow administrativo CURRENT é
`llm-agent extensions`, que delega nos services canônicos.

## Superficie administrativa CURRENT

Use `llm-agent extensions` para administrar a capability moderna:

```text
extensions list
extensions register MANIFEST
extensions enable ID
extensions disable ID
extensions grant ID CAPABILITY
extensions revoke ID CAPABILITY
extensions inspect [ID]
```

Os comandos delegam nos services canonicos e nao editam o registry legado.
`llm-agent tools` continua uma superficie de compatibilidade que usa
`extensions/registry.json`; seu estado nao e consumido pelo bootstrap moderno.
Depois da configuracao, `llm-agent run` ainda exige `--task-authority` explicita
para a tarefa. Approval continua separado e nao repara authority ausente.

O bootstrap degradado preserva a aplicação utilizável quando catálogo ou estado
de workspace estão ausentes; corrupção ou indisponibilidade também congela
somente os builtins e publica diagnóstico tipado. Não concede grants. Na
resolução, a presença de catálogo pode ser `orphaned` e a ativação pode ser
`ready`, `blocked` ou `disabled`, com diagnósticos de manifest, capability ou
grant ausente/não utilizado; somente `ready` é materializado.

## Ausências

MCP não existe neste repositório. Não há arbitrary shell, package install
model-actionable, sandbox universal, hot reload, marketplace ou transport
remoto. Web existe somente pela skill `web_search`; não implica browser/MCP.
