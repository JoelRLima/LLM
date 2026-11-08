# Auditoria de modernização — 26/10/2026

## Escopo e método

Auditoria conduzida com três subagentes `gpt-6-luna`, usados tanto para investigar
quanto para implementar correções. Houve revisão cruzada das mudanças e execução
de testes locais. O inventário encontrou 927 arquivos em `src/llm_agent`, 491 em
`tests` e 105 em `scripts`; esses números descrevem o inventário, não uma revisão
manual linha a linha de todos os arquivos.

O checkout já continha muitas mudanças, inclusive a implementação compartilhada
CLI/Workbench. Elas foram preservadas. Nenhum commit, publicação, remoção de
evidências ou aumento de exceções no baseline foi realizado.

As frentes examinadas foram execução de processos, HTTP/modelo, cache semântico,
consultas interativas, ponte Workbench, limites de artefatos, concorrência de
persistência, CI, dependências e dívida arquitetural registrada.

## Achados corrigidos

| Prioridade | Problema e impacto | Correção e evidência |
| --- | --- | --- |
| Alta | `SemanticMemory.load_index()` deserializava `pickle`: abrir um cache manipulado podia executar código com os privilégios do processo. A exploração depende de o consumidor carregar esse arquivo; não foi demonstrada uma rota remota automática. | JSON versionado e gravação atômica; validação de modelo, versão, caminhos, dimensões e valores finitos antes de trocar o estado. Caches pickle antigos retornam `False` e devem ser reconstruídos. [Implementação](../../../../src/llm_agent/agent/memory/semantic_memory.py), [decoder](../../../../src/llm_agent/agent/memory/semantic_memory_cache.py), [regressões](../../../../tests/unit/runtime/test_semantic_memory_cache.py). |
| Alta | O executor escrevia stdin sincronamente antes de drenar stdout/stderr e monitorar timeout. Um filho com pipes cheios ou que não lesse stdin podia bloquear a execução. | Leitores iniciam antes do writer paralelo; timeout/cancelamento continuam monitorados e cleanup recolhe o writer. Regressões com processos reais e timeout externo: entrada/saída de 1 MiB e filho que não lê stdin. [Executor](../../../../src/llm_agent/execution/command.py), [streams](../../../../src/llm_agent/process/streams.py), [testes](../../../../tests/unit/test_command_executor_stdin.py). |
| Alta | Falhas no marcador de apresentação ou no início da thread podiam perder o completion e deixar consultas permanentemente ocupadas. | Resultado canônico protegido contra falha da apresentação; rollback quando a thread não nasce; submissão preservada quando a thread já nasceu. Extração de helpers mantém C901 ≤10. [Executor](../../../../src/llm_agent/interactive/query_executor.py), [regressões](../../../../tests/unit/interfaces/test_interactive_query_executor.py). |
| Alta | Após timeout da ponte Workbench, uma `SettlementFuture` podia ficar sem consumidor; o resultado permanecia na fila e impedia novas operações. | Coleta de settlement em background após timeout. Se a thread de coleta não iniciar, há uma segunda espera limitada a 2 s. Teste com `InteractiveSession` e `SettlementFuture` reais verifica admissão da consulta seguinte. [Ponte](../../../../src/llm_agent/interfaces/workbench/_bridge_queries.py), [regressões](../../../../tests/unit/interfaces/test_workbench_timeouts.py). |
| Média | `OutputStore` carregava o arquivo inteiro antes de verificar o limite de payload; metadata não tinha limite bruto. Um arquivo local grande podia consumir memória antes da rejeição. | Leitura de no máximo 4 MiB + 1 byte para payload e 64 KiB + 1 byte para metadata, preservando os códigos de erro. [Store](../../../../src/llm_agent/outputs/store.py), [regressões](../../../../tests/unit/outputs/test_wave19_output_store.py). |
| Média | Duas instâncias de `FrecencyStore` incrementavam snapshots antigos e só bloqueavam a escrita: a última gravação podia apagar atualizações anteriores. | Leitura, incremento e gravação dentro do mesmo `InstanceLock`; modo apenas em memória preservado. Regressão intercala gravações de duas instâncias antigas e verifica o resultado no disco. [Store](../../../../src/llm_agent/discovery/store.py), [campanha B29](../../../../tests/unit/engineering/test_wave20b_campaign.py). |
| Média | Respostas HTTP não eram fechadas explicitamente ao terminar/parar streaming ou falhar no parsing. | Fechamento em caminhos de conclusão, erro e encerramento do generator; também fechamento de HTTPError mantendo objeto/status/headers. [Gateway](../../../../src/llm_agent/agent/llm/providers/openai_compatible.py), [regressões](../../../../tests/unit/llm/test_model_gateway.py). |
| Média | Os workflows não declaravam explicitamente os privilégios mínimos do token. | `permissions: contents: read` nos [dois](../../../../.github/workflows/ci.yml) [workflows](../../../../.github/workflows/wave18-installed-product.yml). |
| Média | O conjunto de constraints do CI não fixava `prompt-toolkit` e sua dependência `wcwidth`, embora o compositor fosse dependência direta do produto. | Pins consistentes nos dois locks: `prompt-toolkit==3.0.53` e `wcwidth==0.8.3`, iguais ao ambiente validado. Compatibilidade confirmada nos metadados oficiais do [prompt-toolkit](https://pypi.org/project/prompt_toolkit/3.0.53/) e [wcwidth](https://pypi.org/project/wcwidth/0.8.3/). [Teste de consistência](../../../../tests/policy/test_packaging_policy.py). |
| Baixa | Um relatório JUnit truncado ou ilegível fazia o diagnóstico de falha terminar com traceback. | Anotação legível no GitHub Actions sem mascarar o resultado original da suíte. [Script](../../../../scripts/report_pytest_failures.py), [regressões](../../../../tests/unit/scripts/test_report_pytest_failures.py). |
| Média | O walker de documentação e encoding incluía cópias de instalações locais geradas e reportava links ausentes nessas cópias. | Poda apenas de `.workbench-installed-*` na raiz, alinhada ao `.gitignore`. Regressão garante que arquivos em `prototypes/.../evidence` continuam examinados. O teste global de links passou após a correção. [Gate](../../../../scripts/check_quality.py), [regressão](../../../../tests/policy/test_quality_policy.py). |

## Próximas modernizações, por prioridade

1. **P1 — concluir a seleção determinística do material examinado pelos gates.**
   A inclusão de instalações `.workbench-installed-*` no walker de documentação
   foi corrigida nesta auditoria. O Ruff global ainda encontrou fixtures e snapshots históricos em
   `prototypes/workbench-comparison/evidence`. A execução elevada encontrou 115
   erros de lint nessa árvore; a execução restrita escondia parte deles por ACL.
   Separar fontes ativas, fixtures intencionais e evidências congeladas requer
   inventário explícito. Preservar o teste que detecta código de produção oculto
   por `.gitignore`; excluir `prototypes/` inteiro esconderia código ativo.
   Esse ruído foi comprovado no checkout atual e não prova falha igual em um
   checkout limpo de CI.
2. **P1 — dividir a inferência de efeitos semânticos por responsabilidade.** O
   [baseline](../../../../quality/baseline.json) registra 892 linhas em
   `task_semantics_effect_inference.py`, `_clause_intents` com C901=20 e
   `_classify_path_roles` com C901=12. O arquivo reúne normalização, papéis de
   caminhos, predicados e intenções. Extrair essas responsabilidades com testes
   comportamentais preservados é prioritário por participar da interpretação
   de efeitos e autoridade. Reduzir o baseline conforme as extrações eliminarem
   a dívida, sem transferir exceções para novos módulos.
3. **P2 — dividir as famílias de regras de interação.** `interaction/guards.py`
   tem 763 linhas e quatro exceções C901 (12, 11, 18 e 14). Separar classificação
   operacional, identidade do alvo e conflito entre cláusulas reduz o alcance
   de regressões. Ao todo, o baseline tem 24 exceções de complexidade e 32 de
   tamanho; a dívida está explicitamente registrada, não eliminada pelos gates.
4. **P2 — fixar o backend do build isolado da aceitação instalada.** O host de
   CI recebe `setuptools==81.0.0` pelo lock, mas `pyproject.toml` declara
   `setuptools>=69` e `verify_installed_package.py` usa `pip wheel` com isolamento
   por padrão. As constraints do host não são encaminhadas explicitamente ao
   ambiente de build. Criar uma resolução versionada para esse ambiente sem
   remover o isolamento. Os pins adicionados nesta auditoria não resolvem esse
   problema separado.
5. **P3 — medir cobertura dos pacotes críticos antes de estabelecer metas.** Não
   há medição de cobertura na configuração atual de pytest/CI. Isso é uma
   oportunidade de observabilidade; não demonstra ausência de testes em linhas
   específicas. Começar por execução, autoridade e persistência, medir o estado
   real e depois definir um ratchet proporcional ao risco.

## Compatibilidade e limites

- Python mínimo permanece 3.10; não houve migração de framework nem aumento
  arbitrário de versões mínimas. A matriz CI existente continua Ubuntu/Windows
  com Python 3.10/3.12.
- Caches semânticos pickle não são migrados por deserialização: precisam ser
  reconstruídos. O formato novo continua aceitando o caminho escolhido pelo
  consumidor, independentemente da extensão.
- Dois testes vetoriais requerem NumPy opcional; o ambiente core desta auditoria
  não o possui. A validação do fluxo ML completo permanece pendente.
- O corpo de HTTPError em chamadas em streaming não é materializado antes do
  fechamento; status e headers continuam acessíveis. Não foi encontrado
  consumidor desse corpo no projeto.
- A recuperação após timeout depende de conseguir executar o consumidor de
  settlement. Se a criação da thread falhar e a espera adicional também expirar,
  a recuperação posterior depende dos mecanismos de sessão/fechamento.
- Testes executados no Windows/Python 3.12 não substituem a matriz remota de CI
  nem o teste visual do host WebView2 instalado.

## Validação

- **205 testes passaram; 2 skips** no conjunto integrado das correções: cache,
  gateway, subprocessos, consultas, Workbench, OutputStore, discovery, packaging,
  diagnóstico e integrações reais de sessão/ponte. JUnit:
  `.audit-local/modernization-focused.xml`. Os skips são os dois cenários de
  matriz/roundtrip com NumPy opcional ausente.
- Depois do ajuste final do walker, passaram também a regressão de seleção e o
  teste global de links locais (**2/2**).
- `ruff check src scripts tests` passou. Mypy Linux e Windows passaram em **1.023
  arquivos**. C901 passou nos módulos de produção alterados, sem exceções novas;
  todos eles permanecem com até 300 linhas. `git diff --check` passou, com avisos
  de normalização CRLF/LF em arquivos CLI já modificados anteriormente.
- A tentativa da suíte completa com `-x` terminou em **177 passed, 2 skipped,
  1 failed** após 375,71 s, no teste de links das instalações geradas. Esse
  problema foi corrigido e o teste específico passou depois. A suíte completa
  não foi repetida integralmente; houve ainda um aviso de finalização do Tkinter
  fora da thread principal no teste de ponte instalada.
- Uma execução adicional ampla de testes unitários/política foi interrompida
  após aproximadamente 8% de progresso; não constitui aprovação dessa suíte.
- Os gates globais continuam sem aprovação: há lint/complexidade em snapshots
  e fixtures históricos de `prototypes`, além de C901=16 no script ativo
  `prototypes/workbench-comparison/run_webview_inspection.py::main`, e **28 arquivos** com BOM/encoding fora
  da política nessa árvore. Eles não foram apagados, reescritos nem excluídos
  em bloco para produzir um resultado verde.
- A rodada final de `validate.py static` confirmou essas pendências; mypy nas
  duas plataformas e diff-check passaram. O log bruto registra ainda 10 erros
  de lint em fixtures temporárias criadas pelos subagentes na raiz. Esses
  diretórios foram depois preservados sob `.audit-local/`; a nova execução do
  Ruff global ficou com os **115 achados restantes em `prototypes`**.
- `pip check` passou: nenhuma dependência instalada quebrada foi encontrada.
- O pytest restrito pelo sandbox falhava no setup com WinError 5. As verificações
  consolidadas usaram execução com elevação aprovada automaticamente e diretórios
  temporários isolados. Esse erro de setup não foi tratado como defeito do código.

Logs temporários ficam em `.audit-local/`, ignorado pelo Git. A aprovação da
matriz remota e o fluxo ML completo não são reivindicados por esta auditoria.
