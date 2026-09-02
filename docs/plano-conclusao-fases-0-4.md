# Plano executável para concluir as fases 0–4

> **STATUS: HISTORICAL RUNBOOK.** Não execute como plano CURRENT. Consulte o
> [índice técnico](README.md) e os primary homes para o estado vigente.

> **NOTA DE SUPERSSESSÃO (W8, 2026-09-02).** Os status de Gate e a orientação
> sobre `AutoCoder` neste runbook são uma fotografia pré-W8. A Wave 8 foi
> publicada e fechada; a arquitetura atual e a disposição do Gate 2.7c estão
> descritas no [checkpoint PRE-V1](pre-v1-freeze.md). O texto original abaixo
> é preservado para manter a proveniência histórica.

## 1. Finalidade

Este documento é a fonte operacional para levar o estado atual do repositório
até a conclusão verificável das fases 0–4 da plataforma standalone. Ele foi
escrito para execução incremental por humanos ou modelos com menor capacidade
de contexto.

Não use este arquivo como prova de que uma fase está pronta. Uma tarefa só pode
ser marcada como concluída depois que seus comandos de validação passarem e a
evidência for registrada na própria tarefa.

Documentos de referência:

- [visão da plataforma](plataforma-standalone.md);
- [ADR 0002 — visão e fronteiras](adr/0002-visao-do-assistente-standalone.md);
- [ADR 0003 — bootstrap e lifecycle](adr/0003-bootstrap-paths-e-ciclo-de-vida-standalone.md);
- [operação standalone](operacao-standalone.md);
- [contratos atuais de tools](agent/tools.md).

## 2. Diagnóstico inicial que este plano deve corrigir

No snapshot auditado:

- `pytest`: 383 testes passaram e 15 foram ignorados;
- aceitação do wheel instalado: passou com `--no-build-isolation`;
- `pip check`: passou;
- quality policy: falhou em três funções complexas e um módulo grande;
- Ruff: 13 erros;
- mypy Linux e Windows: 14 erros;
- `git diff --check`: dois problemas;
- o working tree contém muitos arquivos ainda não versionados e um diretório
  acidental chamado `%TEMP%`;
- a documentação diverge sobre o estado das fases 2–4.

Problemas funcionais confirmados:

1. extensions registradas pelo CLI não são carregadas por `AgentApplication`;
2. são construídos dois conjuntos diferentes de instâncias das skills;
3. o adapter builtin converte `blocked`, `cancelled` e `unverified` em
   `failed`;
4. há caminhos que invocam skills sem passar por `ToolInvocationGateway`;
5. o timeout por thread do gateway não encerra nem libera a operação;
6. uma tool externa é rejeitada pelo planner por não existir no dicionário
   legado de skills;
7. adapters podem sobrescrever nomes de tools silenciosamente;
8. a autorização considera a persona, mas não implementa a interseção completa
   entre tarefa, extension, capability, recurso e aprovação;
9. um passo bloqueado pode ser removido do plano sem uma substituição válida;
10. o transport externo ainda não limita ambiente, protocolo e volume de saída
    de forma suficiente.

## 3. Protocolo obrigatório de execução

### 3.1 Regras para qualquer executor

1. Execute somente uma tarefa identificada por vez.
2. Leia integralmente os arquivos listados na tarefa antes de editar.
3. Verifique `git status --short` antes e depois da mudança.
4. Preserve alterações preexistentes não relacionadas.
5. Use `apply_patch` para editar arquivos.
6. Não altere `quality/baseline.json`, limites do Ruff ou configuração do mypy
   para fazer um gate passar.
7. Não adicione `# noqa`, `type: ignore` ou captura genérica de exceção sem uma
   justificativa ligada ao contrato público.
8. Não introduza dependência pesada no runtime core.
9. Não faça commit ou push sem solicitação explícita.
10. Se um teste falhar fora do escopo da tarefa, pare e registre o comando, a
    falha e os arquivos que já foram modificados.

### 3.2 Formato da evidência

Ao concluir uma tarefa, substitua `Evidência: pendente` por:

```text
Evidência: PASS — <comando> — <quantidade de testes ou resumo do gate>
```

Se houver mais de um comando, registre uma linha por comando. Não marque a
checkbox antes disso.

### 3.3 Gates globais

Execute-os somente nos marcos indicados, nesta ordem:

```powershell
.venv\Scripts\python.exe scripts\check_quality.py
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy --platform linux
.venv\Scripts\python.exe -m mypy --platform win32
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe scripts\verify_installed_package.py --no-build-isolation
git diff --check
```

No CI, o gate do wheel deve continuar sendo executado sem
`--no-build-isolation`.

## 4. Ordem de execução

```text
P0 -> P1 -> P2 -> P3 -> P4 -> P5 -> P6 -> P7 -> P8 -> P9 -> P10
```

Não execute fases posteriores para contornar uma falha anterior. Dentro de um
pacote, respeite a ordem numérica das tarefas.

---

## P0 — Higiene e baseline confiável

### P0-01 — Remover artefatos acidentais

- [x] Concluída
- Dependências: nenhuma.
- Arquivos permitidos: `.gitignore`; somente artefatos comprovadamente gerados.
- Não modificar: código de produção.

Ações:

1. Inspecionar `%TEMP%/` e confirmar que contém apenas saída gerada por teste.
2. Remover o diretório acidental sem tocar em um diretório real de sistema.
3. Investigar `.tmp/pytest-of-*` e `tmp_pytest/`; não forçar remoção se houver
   erro de permissão. Corrigir a origem que os cria dentro do checkout.
4. Garantir que testes usem `tmp_path`, `tempfile` ou paths sob `C:\tmp`, nunca
   strings literais como `%TEMP%`.
5. Adicionar ao `.gitignore` apenas padrões de artefatos realmente genéricos;
   não ocultar fontes Python.

Aceite:

```powershell
git status --short
git ls-files --others --exclude-standard
```

O resultado não pode listar `%TEMP%/` nem fontes relevantes ocultas.

Evidência: PASS — `git status --short` e `git ls-files --others --exclude-standard` — artefato `%TEMP%` removido e padrões adicionados ao `.gitignore`.

### P0-02 — Restaurar os gates estáticos

- [x] Concluída
- Dependências: P0-01.
- Arquivos permitidos: arquivos apontados pelos erros de Ruff, mypy e quality.

Ações:

1. Corrigir imports não usados e fora de ordem.
2. Declarar atributos de `AgentApplication` e `Orchestrator` no construtor;
   não depender de atributos adicionados dinamicamente.
3. Não reutilizar o nome `gateway` para gateway de modelo e gateway de tools.
4. Corrigir retornos opcionais de `ExtensionRegistry`.
5. Tipar capabilities como `frozenset[str]` nas bordas canônicas.
6. Dividir, sem mudar comportamento, as funções com complexidade maior que 10.
7. Extrair parsing/dispatch da CLI até `agent/interfaces/cli/app.py` voltar a
   ter no máximo 300 linhas.
8. Corrigir whitespace e linha vazia final apontados por `git diff --check`.

Aceite: os quatro primeiros gates globais e `git diff --check` passam.

Evidência: PASS — `check_quality.py`, Ruff, mypy Linux/Windows e `git diff --check` — todos passaram.

---

## P1 — Fechar a Fase 0: constituição e fontes de verdade

### P1-01 — Unificar o estado documentado do roadmap

- [x] Concluída
- Dependências: P0-02.
- Arquivos permitidos: `docs/plataforma-standalone.md`,
  `docs/phase_0_4_implementation_report.md`, `docs/agent/tools.md`, `README.md`,
  `EstruturaProjeto.md`.

Ações:

1. Manter `ADR 0002` como fonte da visão e do trust model.
2. Transformar o relatório das fases 0–4 em relatório factual: usar
   `entregue`, `parcial` ou `pendente`, com evidência reproduzível.
3. Remover afirmações de gateway único enquanto P6 não estiver concluído.
4. Atualizar a tabela de estado de `plataforma-standalone.md` sem confundir
   existência de classes com integração aceita.
5. Referenciar este plano no relatório e no índice.

Aceite:

```powershell
rg -n "ainda não implementado|próxima fase|fases 0 a 4 foram implementadas|gateway garante" docs README.md EstruturaProjeto.md
.venv\Scripts\python.exe scripts\check_quality.py
```

Cada ocorrência deve ser verdadeira no estado corrente e não contradizer outro
documento.

Evidência: PASS — `rg` nos documentos de estado — tabela e relatório agora distinguem entregue, parcial e pendente.

### P1-02 — Congelar os contratos canônicos mínimos

- [x] Concluída
- Dependências: P1-01.
- Arquivos permitidos: `agent/tools/contracts.py`, ADR novo se a decisão mudar.

O contrato deve representar, sem dicionários paralelos:

- identidade da tool e do adapter/extension;
- versão da tool e do protocolo;
- schema de entrada;
- capabilities e efeitos declarados;
- timeout e suporte a cancelamento;
- `invocation_id`, `task_id` e workspace lógico;
- status `succeeded`, `failed`, `blocked`, `cancelled`, `timed_out`,
  `permission_denied`, `protocol_error`, `unavailable` e `unverified`;
- erro estruturado;
- artifacts e metadados de origem.

Ações:

1. Adicionar `BLOCKED` e `UNVERIFIED` a `ToolStatus`.
2. Definir semanticamente `ok`, `done` e conversão legada para cada status.
3. Tornar collections públicas imutáveis quando possível.
4. Não incluir classes do `Orchestrator` ou da CLI nos contratos.

Testes: `tests/unit/tools/test_contracts.py` deve cobrir todos os status e a
conversão legada.

Evidência: PASS — `pytest -q tests/unit/tools/test_contracts.py` — status, resultado legado e descriptor cobertos.

---

## P2 — Fechar a Fase 1: composição standalone sem regressão

### P2-01 — Construir uma única instância de cada skill

- [x] Concluída
- Dependências: P1-02.
- Arquivos permitidos: `agent/application.py`, `agent/skills/__init__.py`,
  `agent/tools/builtin_adapter.py`, `agent/orchestrator.py` e testes de
  aplicação.

Ações:

1. Em `AgentApplication.create`, construir exatamente um `SkillRegistry`.
2. Passar esse mesmo registro ao `BuiltinToolAdapter`.
3. Registrar no `Orchestrator` as mesmas instâncias, sem chamar
   `load_all_skills()` separadamente.
4. Injetar `orchestrator` apenas depois da composição quando uma skill legada
   ainda precisar dele.
5. Declarar `tool_registry` e `tool_invocation_gateway` nos tipos e
   construtores; não adicioná-los depois da criação.
6. Manter gateway de modelo e gateway de tools em variáveis diferentes.

Testes obrigatórios:

- identidade por `is` entre skill do adapter e skill do orchestrator;
- uma `session_memory` usa a mesma memória da aplicação;
- uma skill que recebe `orchestrator` recebe a instância correta;
- falha durante a composição libera lock e logging.

Evidência: PASS — `pytest -q tests/integration/test_standalone_application.py tests/unit/tools` — composição com um `SkillRegistry` compartilhado passou.

### P2-02 — Preservar status através do adapter builtin

- [x] Concluída
- Dependências: P2-01.
- Arquivos permitidos: `agent/tools/builtin_adapter.py`, contratos e testes.

Ações:

1. Criar uma tabela explícita de conversão de status legado para
   `ToolStatus`.
2. Preservar `blocked`, `cancelled` e `unverified`.
3. Usar `FAILED` somente para falha real.
4. Preservar código e mensagem de erro sem transformar ausência de aprovação
   em erro genérico.
5. Rejeitar resultados de formato impossível como `PROTOCOL_ERROR`.

Testes obrigatórios:

- um teste parametrizado para todos os status;
- `file_writer` sem aprovação retorna `blocked` através do gateway real;
- validator ausente retorna `unverified` através do gateway real.

Evidência: PASS — `pytest -q tests/unit/tools/test_builtin_adapter.py tests/integration/test_standalone_application.py` — status canônicos preservados.

### P2-03 — Validar status headless sem monkeypatch do ciclo

- [ ] Concluída
- Dependências: P2-02.
- Arquivos permitidos: testes de integração standalone e doubles de suporte.

Substituir ou complementar testes que fazem monkeypatch de
`orchestrator.run()` por jornadas reais:

1. plano com `file_writer` e `RequireExplicitApproval` termina `blocked`;
2. o arquivo não é criado;
3. `--yes` permite a mudança;
4. `unverified` nunca produz exit code zero;
5. `cancelled` permanece distinto de `failed`;
6. stdout JSON contém exatamente um documento.

Evidência: pendente.

### P2-04 — Remover efeitos legados fora da autoridade

- [x] Concluída
- Dependências: P2-03.
- Arquivos permitidos: `agent/auto_coder.py`, `agent/workspace.py`,
  `agent/planning/step_policies.py`, composição e testes relacionados.

Ações:

1. Não permitir que `AutoCoder` sobrescreva uma correção diferente da mudança
   aprovada.
2. Não executar testes gerados pelo modelo sem autoridade de processo.
3. Preferência: retirar a autocorreção implícita do pós-processamento e usar o
   workflow explícito `code_task repair`.
4. Se a compatibilidade for mantida, toda execução e segunda escrita devem
   atravessar gateway e `ApprovalPort`, preservando `blocked`/`cancelled`.
5. Substituir `py_compile` que gera `.pyc` por validação sintática sem escrita.
6. Validators opcionais de `WorkspaceManager` devem usar o runner canônico ou
   ser marcados explicitamente como caminho legado não usado no standalone.

Aceite: nenhuma escrita diferente do conteúdo aprovado ocorre em uma jornada
headless ou interativa simulada.

Evidência: PASS — `pytest -q tests/unit/planning/test_step_executor.py tests/integration/test_standalone_application.py` — pós-processamento não faz segunda escrita; validação sintática não cria `.pyc`.

---

## P3 — Fechar a Fase 2: microkernel e extensions realmente utilizáveis

### P3-01 — Tornar `ToolRegistry` determinístico

- [x] Concluída
- Dependências: P2-04.
- Arquivos permitidos: `agent/tools/tool_registry.py`, contracts e testes.

Ações:

1. Rejeitar nome duplicado com erro explícito.
2. Impedir que extension substitua builtin silenciosamente.
3. Definir ordem determinística para `names()` e `descriptors()`.
4. Associar cada descriptor ao `adapter_id` e à versão de origem.
5. Validar nome, schema e capabilities no registro, antes de publicar a tool.
6. Se o registro de um adapter falhar, não publicar nenhuma de suas tools.

Evidência: PASS — `pytest -q tests/unit/tools/test_tool_registry.py` — colisões rejeitadas e nomes ordenados.

### P3-02 — Normalizar schemas builtin

- [x] Concluída
- Dependências: P3-01.
- Arquivos permitidos: descriptors/catálogo de skills, builtin adapter,
  validador de argumentos e testes.

Ações:

1. Converter schemas legados como `{"file_path": "string: ..."}` para um
   subconjunto JSON Schema canônico.
2. Definir e documentar o subconjunto suportado: object, properties, required,
   additionalProperties, string, integer, number, boolean, array e object.
3. Tratar `bool` como inválido para integer/number.
4. Validar estruturas aninhadas ou rejeitar schemas aninhados não suportados.
5. Falhar fechado em schema inválido; não ignorar silenciosamente.

Evidência: PASS — `pytest -q tests/unit/tools/test_builtin_adapter.py` — schemas builtin são publicados como objeto JSON Schema com tipo padrão e propriedades normalizadas.

### P3-03 — Tornar o registro de extensions durável e seguro

- [x] Concluída
- Dependências: P3-01.
- Arquivos permitidos: `agent/tools/extension_registry.py`, paths e testes.

Ações:

1. Separar leitura sem efeitos de `initialize()`/mutação explícita.
2. Persistir atomicamente com tempfile, `fsync` e `os.replace`.
3. Rejeitar JSON corrompido, IDs duplicados, manifest vazio e links finais
   inseguros.
4. `add` deve validar o manifest antes de habilitar a extension.
5. Exigir que `manifest.id` corresponda ao ID registrado.
6. Preservar origem em migrações e nunca apagar registro automaticamente.

Evidência: PASS — `pytest -q tests/unit/tools/test_extension_registry.py` — persistência atômica, entradas inválidas rejeitadas e ID/manifest verificados quando disponíveis.

### P3-04 — Carregar extensions no bootstrap real

- [x] Concluída
- Dependências: P3-02 e P3-03.
- Arquivos permitidos: `AgentApplication`, `AppPaths`, loader de tools, CLI e
  testes de integração.

Ações:

1. Definir uma propriedade canônica para o arquivo de registro em `AppPaths`.
2. Passá-la ao loader usado por `AgentApplication`.
3. Carregar somente extensions habilitadas e manifests válidos.
4. Uma extension inválida não pode deixar a aplicação parcialmente composta;
   definir se falha o bootstrap ou aparece como `unavailable` e documentar a
   decisão.
5. `tools add/enable/disable/list/doctor` e o bootstrap devem usar a mesma fonte
   de verdade.
6. Adicionar jornada real: `tools add -> AgentApplication -> registry`.

Evidência: PASS — `pytest -q tests/integration/test_standalone_application.py::test_application_loads_registered_extension_from_canonical_registry` — CLI e bootstrap usam `AppPaths.extensions_registry_file`.

---

## P4 — Fechar a Fase 3: autorização e transport externo

### P4-01 — Criar contexto de autorização por invocação

- [x] Concluída
- Dependências: P3-04.
- Arquivos permitidos: contracts de tools, novo módulo pequeno de autorização,
  approval e testes.

Criar um valor imutável contendo:

- task/invocation ID;
- persona apenas como sinal de política, nunca como autoridade suficiente;
- capabilities concedidas à tarefa;
- capabilities concedidas à extension;
- recursos permitidos, incluindo workspace;
- nível de autonomia e porta de aprovação;
- versão da política usada.

A decisão efetiva deve ser a interseção:

```text
capability declarada pela tool
∩ grant persistido da extension
∩ autoridade da tarefa
∩ recurso solicitado
∩ aprovação exigida para o efeito
```

A extension não pode conceder autoridade a si própria declarando uma
capability menos restritiva.

Evidência: PASS — `pytest -q tests/unit/tools/test_invocation_gateway.py` — contexto imutável e interseção de capabilities aplicados.

### P4-02 — Aplicar autorização no gateway

- [x] Concluída
- Dependências: P4-01.
- Arquivos permitidos: invocation gateway, authorization, contracts e testes.

Ações:

1. Substituir parâmetros soltos `active_skills`/`allowed_capabilities` pelo
   contexto tipado.
2. Autorizar antes de emitir `tool_start` e antes de iniciar adapter.
3. Retornar `PERMISSION_DENIED` ou `BLOCKED` de forma determinística.
4. Solicitar `ApprovalPort` somente quando a política exigir consentimento.
5. Registrar decisão, capability, recurso e motivo sem registrar secrets.
6. Uma falha na telemetria não pode alterar a decisão de autorização.

Evidência: PASS — `pytest -q tests/unit/tools/test_invocation_gateway.py tests/integration/test_standalone_application.py` — aprovação e negação ocorrem antes do adapter.

### P4-03 — Tornar o gateway a fronteira única no standalone

- [x] Concluída
- Dependências: P4-02.
- Arquivos permitidos: ToolExecutor, handlers CLI, security service,
  orchestration e testes.

Ações:

1. Fazer `/read`, `/find`, `/search` e demais comandos de skill usarem gateway.
2. Fazer security analysis e summarize usarem a mesma função de invocação.
3. Remover o fallback direto da composição standalone.
4. Se compatibilidade legada for necessária, isolá-la em adapter explicitamente
   chamado `LegacyToolInvoker`, não em um `if gateway is None` no fluxo novo.
5. Impedir chamadas diretas a `skill.execute()` fora do builtin adapter, exceto
   testes unitários da própria skill.

Aceite:

```powershell
rg -n "\.execute\(" agent/interfaces agent/orchestration agent/planning agent/tool_executor.py
```

Toda ocorrência restante deve ser adapter, caso de uso que não é tool ou
compatibilidade documentada fora da composição standalone.

Evidência: PASS — `rg -n "\.execute\(" agent/interfaces agent/orchestration agent/planning agent/tool_executor.py` — chamadas diretas restantes estão isoladas em `LegacyToolInvoker` ou são executores de casos de uso.

### P4-04 — Endurecer manifest e protocolo stdio

- [x] Concluída — Gate 1 validado no CI multiplataforma
- Dependências: P4-01.
- Arquivos permitidos: stdio adapter, contracts, example extension e testes.

Ações:

1. Aceitar apenas `transport: stdio` e a versão de protocolo suportada.
2. Validar tipos, campos obrigatórios, IDs, entrypoint, tools, schemas,
   capabilities e limites numéricos.
3. Rejeitar campos desconhecidos no objeto raiz e nas declarações de tools;
   campos internos de `schema` seguem JSON Schema sem filtragem do manifest.
4. Rejeitar manifest com tool duplicada ou conflito global.
5. Enviar e receber exatamente uma mensagem JSONL por invocação.
6. Exigir `invocation_id` igual na resposta.
7. Rejeitar stdout extra, JSON inválido, status desconhecido e resposta acima
   do limite.
8. Truncar stderr apenas para diagnóstico; nunca misturá-lo ao resultado.
9. Incluir extension ID, versão e versão do protocolo no trace.

Evidência: PASS — protocolo stdio, manifest estrito, limites, cleanup,
launcher Windows e conformance aprovados no CI Windows/Ubuntu em Python 3.10
e 3.12.

### P4-05 — Implementar timeout e cancelamento reais

- [x] Concluída
- Dependências: P4-04.
- Arquivos permitidos: gateway, stdio adapter, runtime de processo e testes.

Ações:

1. Remover timeout baseado em thread que espera no `shutdown`.
2. Para subprocessos, usar `Popen`, processo/grupo próprio, polling de
   cancelamento e deadline monotônico.
3. Em timeout/cancelamento: solicitar término, aguardar prazo curto, matar e
   colher o processo.
4. Nunca retornar enquanto a operação continua em background.
5. Para adapter in-process sem cancelamento cooperativo, declarar explicitamente
   que timeout não é suportado; não simular garantia falsa.
6. Limitar stdout e stderr durante a leitura, não somente depois.

Testes devem usar processos curtos e sentinelas para provar que nada continua
escrevendo depois do retorno terminal.

Evidência: PASS — `pytest -q tests/unit/tools/test_stdio_adapter.py` — subprocesso externo usa `Popen`, deadline, término e coleta após timeout.

### P4-06 — Reduzir o ambiente do processo externo

- [x] Concluída
- Dependências: P4-04.
- Arquivos permitidos: runtime de processo/stdio, docs e testes.

Ações:

1. Construir ambiente por allowlist, não copiar `os.environ` inteiro.
2. Não encaminhar tokens, chaves, `PYTHONPATH`, configuração Git, plugins ou
   variáveis de tracing por padrão.
3. Passar workspace e diretórios de scratch por campos explícitos do protocolo.
4. Definir cwd da extension de forma explícita e previsível.
5. Documentar honestamente que subprocesso não é sandbox de sistema
   operacional.

Evidência: PASS — inspeção de `_safe_environment` e `pytest -q tests/unit/tools/test_stdio_adapter.py` — processo recebe somente allowlist operacional.

---

## P5 — Fechar a Fase 4: planejamento e ciclo completo

### P5-01 — Tornar o planner nativo de `ToolRegistry`

- [x] Concluída
- Dependências: P4-03.
- Arquivos permitidos: plan validator, metadata, optimizer, execution gateway,
  replan e testes.

Ações:

1. Validar existência pelo `ToolRegistry`, não por `orchestrator.skills`.
2. Usar descriptor canônico para schema, custo, capabilities e efeitos.
3. Permitir tools externas autorizadas em planos.
4. Manter o dicionário legado somente em adapter de compatibilidade.
5. Revalidar plano depois da otimização e toda substituição do replan.

Evidência: PASS — `pytest -q tests/unit/planning/test_plan_validator.py tests/unit/planning/test_execution_gateway.py` — descriptors do registry validam tools externas.

### P5-02 — Proibir remoção silenciosa de passo bloqueado

- [x] Concluída
- Dependências: P5-01.
- Arquivos permitidos: execution gateway, replan, resultados de plano e testes.

Ações:

1. Se não houver substituição válida, preservar o passo como bloqueado e
   encerrar o objetivo como `blocked`/`failed` conforme o motivo.
2. Nunca apagar o passo e continuar como se o objetivo estivesse completo.
3. Uma substituição deve declarar qual requisito original satisfaz.
4. O resultado final deve listar passos não executados e a causa.

Evidência: PASS — `pytest -q tests/unit/planning/test_execution_gateway.py` — ausência de substituição aborta e preserva o objetivo.

### P5-03 — Unificar eventos e gravação de resultado

- [x] Concluída
- Dependências: P4-03 e P5-01.
- Arquivos permitidos: gateway, StepExecutor, PlanExecutor, AgentState e testes.

Ações:

1. Definir um único owner para `tool_start`, `tool_end` e
   `record_tool_result`.
2. Evitar eventos duplicados no caminho sequencial.
3. Preservar o mesmo comportamento no batch paralelo.
4. Correlacionar evento, resultado, step ID, task ID e invocation ID.
5. Falha ao persistir resultado deve tornar a tarefa falha; não apenas gerar
   warning.

Evidência: PASS — `pytest -q tests/unit/planning/test_step_executor.py tests/unit/planning/test_execution_gateway.py` — caminho standalone deixa eventos e gravação sob o gateway.

### P5-04 — Retomada deve reautorizar

- [ ] Concluída
- Dependências: P4-01 e P5-01.
- Arquivos permitidos: checkpoint contracts, AgentState, TaskRunner,
  Orchestrator e testes.

Ações:

1. Persistir identidade da política e grants referenciados, não autoridade
   efetiva como verdade confiável.
2. Ao retomar, recalcular capabilities com a política atual.
3. Revalidar extension habilitada, versão, descriptor e recursos.
4. Se a autorização diminuiu, terminar `blocked` antes de repetir o efeito.
5. Não repetir automaticamente uma invocação cujo efeito anterior seja
   incerto.
6. Persona e prompt restaurados não podem conceder autoridade sozinhos.

Evidência: pendente.

### P5-05 — Fechar a jornada ponta a ponta de extension

- [ ] Concluída
- Dependências: P5-02, P5-03 e P5-04.
- Arquivos permitidos: testes de integração, fixtures, example extension e
  pequenas correções reveladas pelo teste.

Jornada obrigatória:

```text
registrar manifest
-> habilitar extension
-> criar AgentApplication fora do checkout
-> descobrir descriptor
-> gerar/fornecer plano
-> validar schema e autorização
-> invocar por stdio
-> receber resultado estruturado
-> registrar trace/artifact
-> persistir checkpoint/memória controlada
-> encerrar sem processo residual
```

Variações obrigatórias:

- capability negada;
- schema inválido;
- versão incompatível;
- timeout;
- cancelamento;
- stdout excessivo;
- processo com exit code diferente de zero;
- retomada após interrupção sem repetir efeito incerto.

Evidência: pendente.

---

## P6 — Hardware limitado e eficiência

### P6-01 — Preservar o perfil de 8 GB

- [ ] Concluída
- Dependências: P5-05.
- Arquivos permitidos: hardware/config/runtime e testes.

Confirmar:

- uma inferência ativa;
- um validator/processo pesado ativo;
- extensions carregadas sem modelo adicional;
- descriptors e manifests carregados sem importar dependências pesadas;
- semantic memory continua opcional;
- nenhuma etapa nova duplica a sessão ou o gateway de modelo.

Evidência: pendente.

### P6-02 — Evitar trabalho duplicado

- [x] Concluída
- Dependências: P6-01.
- Arquivos permitidos: composição, planner e testes de contagem.

Ações:

1. Construir skill/adapter apenas uma vez.
2. Validar schema uma vez na borda e trabalhar com valor normalizado.
3. Não executar autocorreção implícita depois de workflow já validado.
4. Não consultar modelo para seleção determinística de tool conhecida.
5. Não carregar processo de extension até a invocação.

Evidência: PASS — `pytest -q tests/integration/test_standalone_application.py tests/unit/tools` — composição usa uma instância compartilhada de skills e adapters.

---

## P7 — Testes de conformance e regressão

### P7-01 — Criar suíte comum de adapters

- [ ] Concluída
- Dependências: P5-05.
- Arquivos permitidos: `tests/contract/` ou grupo equivalente e doubles.

Todo adapter deve passar pelos mesmos casos:

- descriptor válido;
- sucesso;
- argumentos inválidos;
- indisponibilidade;
- falha estruturada;
- status preservado;
- timeout/cancelamento conforme suporte;
- artifacts e invocation ID;
- ausência de efeito quando autorização nega.

Evidência: pendente.

### P7-02 — Cobrir invariantes que os testes atuais não enxergam

- [ ] Concluída
- Dependências: P7-01.

Adicionar regressões para:

- status reais sem monkeypatch do `Orchestrator.run`;
- colisão de nome builtin/extension;
- extension registrada sendo carregada pela aplicação;
- planner aceitando tool externa autorizada;
- planner rejeitando a mesma tool sem grant;
- timeout sem thread/processo residual;
- CLI não invocando skill diretamente;
- um único evento start/end e um único registro de resultado;
- replan sem substituição não removendo objetivo;
- resume reautorizando com política alterada.

Evidência: pendente.

---

## P8 — Gate do artefato instalado

### P8-01 — Ampliar o probe do wheel

- [ ] Concluída
- Dependências: P7-02.
- Arquivos permitidos: `scripts/verify_installed_package.py`, testes policy e
  example extension.

Além das verificações atuais, o wheel instalado deve:

1. registrar uma extension de referência fora do checkout;
2. carregá-la em uma nova `AgentApplication`;
3. invocá-la pelo gateway;
4. negar uma capability não concedida;
5. comprovar que site-packages, cwd externo e sentinela não mudaram;
6. comprovar que nenhum processo ficou vivo;
7. importar apenas do wheel instalado.

O modo default continua resolvendo dependências em venv limpo. O modo offline
continua sendo somente diagnóstico.

Evidência: pendente.

---

## P9 — Documentação final

### P9-01 — Sincronizar documentação técnica

- [ ] Concluída
- Dependências: P8-01.
- Arquivos permitidos: documentação e exemplos.

Atualizar, no mínimo:

- README;
- `EstruturaProjeto.md`;
- `docs/plataforma-standalone.md`;
- `docs/operacao-standalone.md`;
- `docs/agent/tools.md`;
- `docs/agent/orchestration.md`;
- `docs/skills.md`;
- `docs/testes.md`;
- relatório das fases 0–4;
- exemplo de manifest.

Cada documento deve separar claramente:

- comportamento entregue;
- compatibilidade legada;
- limitação conhecida;
- trabalho futuro.

Não declarar sandbox de SO, cancelamento ou gateway único sem um teste que
prove a garantia correspondente.

Evidência: pendente.

---

## P10 — Aceitação final das fases 0–4

### P10-01 — Executar todos os gates

- [ ] Concluída
- Dependências: todas as tarefas anteriores.

Executar os gates globais da seção 3.3 e também:

```powershell
.venv\Scripts\python.exe -m pip check
git status --short
```

Critérios finais:

- zero dívida nova de complexidade;
- zero módulo de produção acima de 300 linhas;
- Ruff e mypy Linux/Windows sem erro;
- suíte completa sem falha;
- skips documentados e restritos a plataforma/dependência opcional;
- wheel limpo aprovado;
- `git diff --check` limpo;
- nenhum artefato temporário versionável;
- documentação sem contradições;
- nenhuma extension externa necessária para o TCC está acoplada ao core.

Evidência: pendente.

### P10-02 — Emitir relatório final factual

- [ ] Concluída
- Dependências: P10-01.

Atualizar `docs/phase_0_4_implementation_report.md` com:

- commit ou snapshot auditado;
- comandos e resultados exatos;
- matriz fase/requisito/evidência;
- limitações residuais;
- riscos aceitos;
- próximos passos a partir da fase 5.

Somente depois dessa tarefa o repositório pode declarar as fases 0–4
concluídas.

Evidência: pendente.

## 5. Condições que exigem decisão humana

Pare e peça decisão antes de:

- adicionar dependência de runtime;
- alterar formato persistido incompatível sem migração;
- permitir rede para uma extension por padrão;
- escolher entre abortar o bootstrap ou degradar uma extension inválida;
- remover uma fachada pública legada;
- implementar sandbox específica do sistema operacional;
- apagar estado real do usuário;
- ampliar o escopo para fase 5 ou integração do TCC.

## 6. Resultado esperado

Ao final, o assistant deve continuar leve para a GTX 1070 de 8 GB, independente
de modelo e capaz de:

- executar fora do checkout;
- carregar tools builtin e externas pelo mesmo contrato;
- negar efeitos sem autoridade;
- preservar status e evidências de ponta a ponta;
- validar e executar planos sem caminhos alternativos;
- retomar tarefas sem repetir efeitos incertos;
- integrar futuramente o TCC como uma extension externa comum.

## 7. Estado da execução deste snapshot

Implementadas e evidenciadas: P0-01, P0-02, P1-01, P1-02, P2-01, P2-02,
P3-01, P3-02, P3-03, P3-04, P4-01, P4-02, P4-03, P4-05, P4-06, P5-01,
P5-02, P5-03 e P6-02.

Ainda pendentes: P2-03, P2-04, P5-04, P5-05, P6-01, P7-01, P7-02,
P8-01, P9-01, P10-01 e P10-02. Esses itens não devem ser marcados como
concluídos apenas porque os gates estáticos passaram; eles exigem as jornadas
e invariantes descritos nas próprias tarefas.

Gate 1: concluído e validado no CI multiplataforma
Gate 2.1: concluído, validado e publicado
Gate 2.2a: concluído, validado e publicado
Gate 2.2b–2.2f: concluído e validado
Gate 2.2: concluído, validado e publicado
Gate 2.3: concluído, validado e publicado
Gate 2.4: concluído, validado e publicado
Gate 2.5: concluído, validado e publicado
Gate 2.6a: concluído, validado e publicado
Gate 2.6b: aprovado localmente, pendente de consolidacao
Gate 2.6c: patch final implementado e autoauditado, pendente de consolidacao
Gate 2.6: não concluído
Gate 2.7a: concluído, validado e publicado
Gate 2.7b: não iniciado
Gate 2.7c: não iniciado
Gate 2.7: não concluído
Gate 2.8: não iniciado
Gate 2.9: não iniciado

Decomposição restante do Gate 2:

- 2.4 materialização runtime;
- 2.5 composição do registry e bootstrap;
- 2.6 descoberta pelos planners;
- 2.7 autorização e invocação pelo gateway;
- 2.8 administração e diagnóstico;
- 2.9 acceptance final instalado.
