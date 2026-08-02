# Continuação executável para encerrar as fases 0–4

## 1. Objetivo e escopo

Este runbook contém somente o trabalho ainda necessário depois da execução de
[plano-conclusao-fases-0-4.md](plano-conclusao-fases-0-4.md). Ele foi escrito
para execução por humanos ou modelos com contexto limitado.

Não refaça tarefas já marcadas como concluídas no plano anterior. Não inicie a
fase 5 e não integre o repositório do TCC neste ciclo.

Snapshot verificado antes deste documento:

- quality gate: passou, sem dívida de complexidade ou módulos grandes;
- Ruff: passou;
- mypy Linux e Windows: passou em 196 arquivos;
- pytest: 385 passaram e 15 foram ignorados;
- `git diff --check`: passou, com avisos não bloqueantes de CRLF/LF;
- tarefas ainda abertas: P2-03, P2-04, P4-04, P5-04, P5-05, P6-01,
  P7-01, P7-02, P8-01, P9-01, P10-01 e P10-02.

## 2. Protocolo obrigatório para o executor

1. Execute somente uma tarefa `Cxx-yy` por vez.
2. Leia todos os arquivos permitidos antes de editar.
3. Rode os testes indicados antes e depois da mudança.
4. Edite somente os arquivos permitidos pela tarefa.
5. Preserve alterações preexistentes e arquivos não relacionados.
6. Use `apply_patch` para edições manuais.
7. Não mude baseline, limites de qualidade, skips ou configurações para ocultar
   falhas.
8. Não adicione dependência de runtime sem decisão humana.
9. Não use `# noqa`, `type: ignore` ou exceção genérica para contornar contrato.
10. Não marque a tarefa como concluída antes de registrar evidência reproduzível.
11. Se um teste anterior ficar vermelho, pare; não avance para outra tarefa.
12. Não faça commit ou push sem solicitação explícita.

Formato de encerramento de uma tarefa:

```text
- [x] Concluída
Evidência: PASS — <comando> — <resultado exato>
Arquivos alterados: <lista>
```

Ordem obrigatória:

```text
C00 -> C10 -> C20 -> C30 -> C40 -> C50 -> C60 -> C70 -> C80
```

## C00 — Congelar o baseline desta continuação

### C00-01 — Confirmar estado inicial

- [x] Concluída
- Dependências: nenhuma.
- Arquivos permitidos: nenhum.

Execute:

```powershell
.venv\Scripts\python.exe scripts\check_quality.py
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy --platform linux
.venv\Scripts\python.exe -m mypy --platform win32
.venv\Scripts\python.exe -m pytest -q
git diff --check
```

Aceite: resultados iguais ou melhores que o snapshot da seção 1. Registre o
número atual de testes; não copie `385` sem executar.

Evidência: PASS — `check_quality.py`, Ruff, mypy Linux/Windows, pytest e `git diff --check` — quality/Ruff/mypy passaram; 385 testes passaram e 15 foram ignorados.

## C10 — Status headless e autoridade de pós-processamento

### C10-01 — Substituir testes headless artificiais

- [ ] Concluída
- Dependências: C00-01.
- Arquivos permitidos: `tests/integration/test_standalone_application.py`,
  doubles em `tests/support/` e pequenas correções reveladas pelo teste.

Problema confirmado: os testes de escrita e resultado `unverified` ainda
substituem `Orchestrator.run` e, em um caso, invocam a skill diretamente.

Ações:

1. Remover monkeypatch de `application.orchestrator.run` dos cenários de status.
2. Fornecer um model gateway hermético que gere um plano real de um passo.
3. Executar o plano pelo `AgentApplication.run` normal.
4. Provar sem acesso a stdin:
   - escrita sem aprovação termina `blocked` e não cria arquivo;
   - `AutoApprove` permite exatamente a escrita solicitada;
   - validator indisponível termina `unverified`;
   - cancelamento termina `cancelled`, nunca `failed`;
   - JSON headless contém um documento e retorna exit code não zero quando não
     houver sucesso.
5. Não chamar `skill.execute()` no teste de integração.

Validação:

```powershell
.venv\Scripts\python.exe -m pytest -q tests\integration\test_standalone_application.py tests\unit\interfaces\test_cli_v2.py
rg -n "monkeypatch.*orchestrator|orchestrator.*run|skills\[.*\]\.execute" tests\integration\test_standalone_application.py
```

Aceite: nenhuma ocorrência relacionada aos cenários de status.

Evidência: pendente.

Progresso parcial deste snapshot: a jornada de escrita `blocked` já usa o
roteamento e o gateway reais; o cenário `unverified` ainda usa monkeypatch e
deve ser substituído por uma fixture de adapter antes de marcar esta tarefa.

### C10-02 — Remover autocorreção implícita do pós-processamento

- [x] Concluída
- Dependências: C10-01.
- Arquivos permitidos: `agent/planning/step_policies.py`, `agent/auto_coder.py`,
  `agent/orchestration/operations.py`, testes correspondentes e docs de código.

Problema confirmado: depois de `file_writer`, `StepPolicies.post_process` chama
`_test_and_correct`, que pode gerar testes, consultar o modelo e escrever uma
segunda versão diferente daquela aprovada.

Ações:

1. Retirar `_test_and_correct` do pós-processamento automático de `file_writer`.
2. Manter validação somente-leitura de sintaxe/lint depois da escrita.
3. Direcionar correções para o workflow explícito `code_task repair`.
4. Manter `AutoCoder` somente como fachada legada documentada, sem uso na
   composição standalone.
5. Provar que uma escrita aprovada não sofre segunda escrita automática.
6. Provar que validação falha produz `unverified`/`failed` sem alterar conteúdo.

Validação:

```powershell
.venv\Scripts\python.exe -m pytest -q tests\unit\planning\test_step_executor.py tests\unit\code tests\integration\test_standalone_application.py
rg -n "_test_and_correct\(" agent\planning agent\orchestration
```

Aceite: nenhuma chamada no fluxo standalone; fachada legada pode permanecer.

Evidência: PASS — `pytest -q tests/unit/planning/test_step_executor.py tests/integration/test_standalone_application.py` — 25 passaram; pós-processamento não chama `_test_and_correct`.

## C20 — Protocolo externo estrito

### C20-01 — Decidir compatibilidade de `invocation_id`

- [x] Concluída
- Dependências: C10-02.
- Arquivos permitidos: ADR novo e documentação de protocolo.

Decisão registrada após a confirmação de que o sistema ainda está em
desenvolvimento, possui somente componentes sob controle do projeto e não tem
consumidores externos do protocolo: manter a versão atual `1.0` e exigir
`invocation_id` em toda resposta associada a uma invocação. Respostas sem ID,
divergentes ou desconhecidas são erros de protocolo. Não há fallback legado e
não se cria `1.1` nesta alteração.

Evidência: `docs/adr/0004-invocation-id-protocolo-stdio-1-0.md` e os testes de
conformance em `tests/unit/tools/test_stdio_adapter.py`.

### C20-02 — Implementar framing, limites e manifest da versão escolhida

- [x] Concluída — Gate 1 validado no CI multiplataforma
- Dependências: C20-01.
- Arquivos permitidos: `agent/tools/stdio_adapter.py`, helpers stdio/processo
  (`agent/tools/stdio_process.py`, `agent/tools/process_tree.py`), contratos,
  example extension, testes de tools e ADR aprovado.

Ações:

1. Validar manifest sem coerções permissivas de tipos.
2. Exigir IDs, versão, transport, entrypoint e lista de tools válidos.
3. Validar schemas e capabilities antes de publicar descriptors.
4. Exigir exatamente uma requisição e uma resposta JSONL.
5. Na versão estrita, exigir `invocation_id` idêntico.
6. Ler stdout/stderr com limite durante a produção, não apenas depois de
   `communicate()` alocar tudo.
7. Encerrar processo ao ultrapassar qualquer limite.
8. Limitar stderr e nunca incluí-lo como dado de sucesso.
9. Atualizar a extension de exemplo para ecoar `invocation_id`.
10. Adicionar testes para stdout extra, JSON inválido, ID ausente/divergente,
    status desconhecido, excesso de stdout/stderr, timeout e processo órfão.

O patch corretivo mantém a implementação stdio aprovada: stdout e stderr são
drenados em paralelo com limites independentes de 1 MiB durante a produção;
qualquer excesso solicita encerramento da árvore. POSIX usa SIGTERM seguido de
SIGKILL no grupo e Windows usa Job Object com `KILL_ON_JOB_CLOSE`. No Windows,
um launcher interno bloqueado é associado ao Job Object antes de receber o
envelope privado que inicia a extension real; falha de associação impede a
execução e não há fallback direto. Falhas de terminação, Job Object e drenagem
são observáveis como `CLEANUP_ERROR`; falha isolada ao remover o status privado
gera diagnóstico bounded sem substituir o resultado principal. O
manifest exige `transport: "stdio"` e `timeout_seconds` explícitos, rejeita
tipos coercivos e valida tools, schemas, capabilities e custo. O exemplo
continua ecoando obrigatoriamente o `invocation_id` recebido.

O Gate permanece aberto até haver evidência do CI Windows e Linux. O teste
POSIX usa readiness explícito antes do timeout, mas permanece skipped no
Windows; a execução local não substitui a confirmação Ubuntu do CI.

Validação:

```powershell
.venv\Scripts\python.exe -m pytest -q tests\unit\tools\test_stdio_adapter.py tests\unit\tools\test_stdio_launcher.py tests\unit\tools\test_stdio_extension_example.py tests\unit\tools\test_extension_registry.py
```

Evidência: PASS — suíte local do adapter, launcher, extension de exemplo e
registry; suíte integral; CI Windows/Ubuntu em Python 3.10/3.12 e acceptance do
ambiente do CI aprovados.

Status operacional: `C20-02: CONCLUÍDO — Gate 1 validado no CI multiplataforma`.

Gate 1: concluído e validado no CI multiplataforma
Gate 2.1: implementado e aprovado localmente
Gate 2.2: não iniciado

## C30 — Retomada segura

### C30-01 — Persistir referências de política, não autoridade efetiva

- [ ] Concluída
- Dependências: C20-02.
- Arquivos permitidos: contratos de checkpoint, `agent/state.py`,
  `agent/checkpoint_manager.py`, autorização e testes de runtime.

Ações:

1. Versionar o checkpoint se o formato mudar.
2. Persistir `policy_version`, IDs dos grants, extension ID/versão e identidade
   do descriptor usados por passos já iniciados.
3. Não persistir persona ou capabilities calculadas como autoridade confiável.
4. Migrar ou rejeitar versões anteriores de forma explícita.
5. Representar efeitos incertos de invocações interrompidas.

Pare antes de alterar formato incompatível sem migração explícita.

Evidência: pendente.

### C30-02 — Reautorizar antes de retomar

- [ ] Concluída
- Dependências: C30-01.
- Arquivos permitidos: `TaskRunner`, `AgentState`, `Orchestrator`, gateway,
  loader de extensions e testes.

Ações:

1. Recarregar política, registry e manifests atuais.
2. Revalidar tool, versão, schema, capabilities e recursos de cada passo
   pendente.
3. Recalcular grants; não reutilizar `allowed_capabilities` restaurado.
4. Se autoridade diminuiu, terminar `blocked` antes do adapter.
5. Não repetir invocação cujo efeito anterior seja incerto.
6. Provar que desabilitar ou trocar a versão de uma extension entre checkpoint
   e resume bloqueia a retomada.

Validação:

```powershell
.venv\Scripts\python.exe -m pytest -q tests\unit\runtime\test_agent_state.py tests\unit\runtime\test_checkpoint_manager.py tests\unit\orchestration\test_task_runner.py tests\integration\test_standalone_application.py
```

Evidência: pendente.

## C40 — Jornada externa e conformance

### C40-01 — Criar suíte comum de adapters

- [ ] Concluída
- Dependências: C30-02.
- Arquivos permitidos: `tests/contract/`, doubles e correções mínimas em
  adapters/contratos.

Crie uma suíte parametrizada aplicável a builtin e stdio com:

- descriptor válido e origem identificada;
- sucesso e falha estruturada;
- argumentos inválidos;
- indisponibilidade;
- preservação de todos os status;
- artifacts e `invocation_id`;
- autorização negada sem efeito;
- timeout/cancelamento conforme suporte declarado.

Evidência: pendente.

### C40-02 — Fechar jornada real de extension

- [ ] Concluída
- Dependências: C40-01.
- Arquivos permitidos: testes de integração, fixtures e example extension.

Teste fora do checkout:

```text
registrar -> habilitar -> criar AgentApplication -> descobrir descriptor
-> validar plano/autorização -> invocar stdio -> registrar resultado/artifact
-> checkpoint controlado -> encerrar sem processo residual
```

Inclua: capability negada, schema inválido, versão incompatível, timeout,
cancelamento, saída excessiva, exit code não zero e retomada sem repetição de
efeito incerto.

Evidência: pendente.

## C50 — Perfil de 8 GB

### C50-01 — Transformar restrições de hardware em invariantes testadas

- [ ] Concluída
- Dependências: C40-02.
- Arquivos permitidos: composição, hardware/runtime e testes de contagem.

Prove com doubles contadores:

1. uma `ChatSession` e um model gateway por aplicação;
2. uma instância por skill e adapter;
3. no máximo uma inferência pesada ativa;
4. validator pesado com concorrência máxima um;
5. extension não inicia processo na descoberta;
6. carregar manifest não importa dependência ML;
7. semantic memory permanece opcional.

Não execute benchmark pesado na GTX 1070 como parte da suíte padrão.

Evidência: pendente.

## C60 — Wheel com extension externa

### C60-01 — Ampliar o gate do pacote instalado

- [ ] Concluída
- Dependências: C50-01.
- Arquivos permitidos: `scripts/verify_installed_package.py`, testes policy e
  example extension.

No venv limpo, fora do checkout:

1. criar/copiar extension de referência;
2. registrar pela CLI instalada;
3. criar nova aplicação;
4. descobrir e invocar a tool externa;
5. negar capability não concedida;
6. comprovar que cwd, site-packages e sentinela não mudaram;
7. comprovar que não restou processo vivo;
8. verificar que imports vêm somente do wheel.

Validação:

```powershell
.venv\Scripts\python.exe scripts\verify_installed_package.py --no-build-isolation
.venv\Scripts\python.exe -m pytest -q tests\policy\test_installed_package_gate.py
```

No CI, mantenha a execução sem `--no-build-isolation`.

Evidência: pendente.

## C70 — Documentação final

### C70-01 — Sincronizar fontes de verdade

- [ ] Concluída
- Dependências: C60-01.
- Arquivos permitidos: documentação e exemplos.

Atualize README, `EstruturaProjeto.md`, plataforma, operação, tools,
orchestration, skills, testes, perfil de hardware e relatório das fases 0–4.

Cada garantia deve apontar para teste ou comando. Separe comportamento
entregue, legado, limitação e futuro. Não declare sandbox de SO.

Validação:

```powershell
rg -n "ainda não implementado|próxima fase|gateway único|todas as execuções|fases 0 a 4 foram implementadas" README.md EstruturaProjeto.md docs
.venv\Scripts\python.exe scripts\check_quality.py
```

Evidência: pendente.

## C80 — Aceitação e encerramento

### C80-01 — Executar aceitação integral

- [ ] Concluída
- Dependências: C70-01.
- Arquivos permitidos: nenhum, salvo correção da causa de uma falha.

Execute, em ordem:

```powershell
.venv\Scripts\python.exe scripts\check_quality.py
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy --platform linux
.venv\Scripts\python.exe -m mypy --platform win32
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe scripts\verify_installed_package.py --no-build-isolation
.venv\Scripts\python.exe -m pip check
git diff --check
git status --short
```

Aceite: todos passam; temporários não aparecem; skips estão justificados; TCC
continua externo ao core.

Evidência: pendente.

### C80-02 — Emitir relatório final

- [ ] Concluída
- Dependências: C80-01.
- Arquivos permitidos: `docs/phase_0_4_implementation_report.md` e os dois
  runbooks.

Registre snapshot, comandos, resultados exatos, matriz requisito/evidência,
limitações e riscos aceitos. Marque as tarefas originais correspondentes como
concluídas somente após a evidência. Só então declare fases 0–4 encerradas.

Evidência: pendente.

## 3. Condições de parada para decisão humana

Pare antes de:

- escolher estratégia incompatível de protocolo;
- alterar checkpoint sem migração;
- adicionar dependência de runtime;
- conceder rede por padrão;
- remover fachada pública legada;
- implementar sandbox específica de SO;
- apagar estado real;
- iniciar fase 5 ou integrar o TCC.
