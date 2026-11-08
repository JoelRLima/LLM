# Avaliação: generalizar a sessão interativa hoje localizada no CLI

**É viável e é a direção recomendada.** O CLI já contém uma camada de coordenação de sessão que pode atender terminal e WebView2. A localização em `interfaces/cli` não torna todo esse código específico de terminal. Parte importante da aplicação já foi generalizada: há catálogo de ações, contratos de consultas, serviços de workspace, outputs, submissão, continuidade, configuração e inspeção.

Esta rodada é uma avaliação com experimentos e testes, não uma refatoração de produção. Não foram alterados CLI, Tk, application, contratos ou comportamento do WebView2. As evidências anteriores foram preservadas.

## O que a comparação demonstrou

O registro atual possui **46 ações**: 32 CONTROL, 4 ATTENTION_ACTION, 6 QUERY e 4 AGENTIC. **42 são marcadas `model_use=NEVER`**. Isso é política declarada do catálogo/bindings, não afirmação de que 42 recursos foram testados integralmente nesta rodada.

O CLI não se resume à chamada de modelo. `interactive_resources.configure()` compõe controller, aprovação, mailbox, projeção de execução, consultas e outputs; `interactive_admission.handle_input()` escolhe a rota antes de executar. O Workbench atual usa o mesmo runtime, mas `WorkbenchSession._execute()` envia toda entrada por `execute_submission(..., entry="natural")`. Ele não faz a admissão dos comandos do CLI nem compõe seus serviços interativos.

Portanto, **mesmo endpoint não significa mesma experiência ou mesmas capacidades**. No WebView2, `/ls`, `/read` e `/model`, por exemplo, não passam pelos mesmos handlers locais do CLI. São entregues como texto à submissão natural; não há garantia de comportamento equivalente nem da política local de não usar modelo.

O nome “modo real” introduzido anteriormente descreve o transporte/runtime, não paridade com o CLI. A avaliação anterior da integração do endpoint tinha esse alcance menor.

## Mapa de reaproveitamento

| Componente atual | O que já faz | Generalização recomendada |
|---|---|---|
| `interfaces/cli/controller.py` | Um worker, geração, estados RUNNING/WAITING_ATTENTION/CANCELLING/SETTLING, cancelamento e settlement com timeout | Extrair como controller compartilhado; não criar outro equivalente no JavaScript |
| `controller_models.py` | Envelopes, resultados de admissão, pendências limitadas e envio que consome a pendência | Extrair os tipos e o armazenamento de sessão; preservar identificadores e semântica |
| `worker_stream.py` | FIFO limitado por itens/caracteres, geração, diagnóstico versus resposta e truncamento | Extrair o transporte; cada UI decide como renderizar |
| `attention.py` | Uma aprovação pendente, fingerprint dos argumentos/diff, geração e invalidação | Extrair o broker; terminal e GUI fornecem suas próprias telas de revisão |
| `ui_mailbox.py` | Transporte de projeções com coalescência e contadores de descarte | Compartilhar como projeção de apresentação; não usá-lo como registro canônico de settlement |
| `ui_plane.py` | Snapshot de execução com atividade, ferramenta, invocação, aprovação e barreira terminal | Extrair snapshot/transições; manter `render_toolbar(width=...)` no adaptador terminal |
| `query_executor.py` | Um worker de consulta, cancelamento, correlação por workspace/geração e resultado pendente | Extrair o scheduler; separar o texto `LIVE SNAPSHOT` dos metadados e manter aliases dos tipos `CliQuery*` |
| `interactive_worker.py` | Liga cancelamento, streams de diagnóstico/resposta, atenção e os use cases canônicos | Separar o executor de operação do `CommandContext` e de bindings CLI; reaproveitar `application.run_interactive_worker` |
| `interactive_admission.py` | Distingue controle, consulta, operação com modelo, estado ocupado e comandos desconhecidos | Separar decisão de admissão de mensagens no console e prompts; receber ação/payload tipados |
| `action_registry.py` | Caminhos/aliases CLI junto de políticas de estado, uso de modelo e mutação | Manter sintaxe CLI no adaptador; mover as políticas compartilháveis para um único owner neutro referenciado por action id |
| `interactive_resources.py` | Composição e encerramento ordenado dos recursos da sessão | Generalizar o ciclo de vida, mantendo shell, first-run visual e renderização nos adaptadores |

Os primeiros sete componentes têm poucas dependências diretas de UI. A análise de imports, isoladamente, não prova independência semântica: `RunViewModel.render_toolbar()` e o texto de snapshot de consultas são exemplos de detalhes de terminal que ainda precisam ser separados.

## O que já é geral e não precisa ser refeito

- `actions/` publica o catálogo neutro. Já existe teste de um futuro adaptador enumerando ações sem importar o CLI.
- `application/services/queries.py` oferece listagem, leitura, busca e consultas Git, com limites, cancelamento, erros tipados e confinamento ao workspace.
- `application/services/composition.py` compõe `ReadOnlyWorkspaceQueryService` e `OutputService` para o workspace.
- `application/task_execution.py` mantém startup, submissão, cancelamento e `bind_interactive_services`, que publica `TaskActivityUpdate` e recebe um presenter de aprovação.
- `application/conversation.py` contém leitura da conversa, configuração, histórico e turnos.
- `application/task_context.py`, `task_continuity.py` e `inspection/` já dão acesso a estado/continuidade e dados de inspeção. O painel Tarefas deve projetar esses dados e a execução atual, sem inventar outro banco de tarefas.
- `application/model_profile_selection.py`, `configuration_admin.py`, `workspace_recents.py` e os use cases de first-run/diagnóstico já concentram operações que a GUI pode apresentar de outro modo.
- `application/workbench/artifacts.py` e `inventory.py` já fornecem relatórios, outputs e inventário de ferramentas. Até a sessão usada pelo Tk possui `outputs()` e `inventory()`; a bridge WebView2 não os expõe hoje.

## Onde colocar a parte comum

Proponho um pacote pequeno **`llm_agent.interactive`** — nome proposto, ainda não criado — para coordenação de sessão compartilhada:

```text
interfaces/cli ───────┐
                     ├── interactive ── application ── Agent/serviços existentes
interfaces/webview ──┘         │
                              └── catálogo/políticas de ações
```

Ele deve depender dos use cases/contratos de application e das políticas neutras, sem importar terminal, HTML, Tk, WebView2 ou módulos `interfaces.cli`. Application não deve importar esse pacote de apresentação/coordenação, evitando inversão de dependências e ciclos.

Uma fachada de sessão pode expor operações como `submit_action(action_id, payload)`, `submit_text(text)`, `query(request)`, `request_cancel(generation)`, pendências, `resolve_attention(identity, decision)`, `snapshot()`, leitura de eventos e `close(timeout)`. Os nomes são ilustrativos: a implementação deve aproveitar os tipos atuais antes de introduzir outros. O frontend recebe dados serializáveis; não recebe objetos privados do Agent nem exceptions/owners arbitrários.

O CLI continua responsável por `/comandos`, aliases, prompts, atalhos, Rich e prompt-toolkit. A GUI liga seus botões diretamente aos action ids e renderiza os mesmos resultados/snapshots. Se oferecer slash commands, isso precisa de um adaptador explícito de sintaxe, não de encaminhamento indiscriminado ao modelo.

Isso não requer mover toda a CLI para application. Operações de negócio continuam nos owners existentes; controller, pendências e projeções são coordenação da sessão. Os handlers que imprimem ou pedem entrada não devem ser importados e executados diretamente pelo navegador.

## Cuidados que impedem uma simples movimentação de arquivos

1. **Uma autoridade por política.** Extrair as políticas de `CliActionBinding` sem copiá-las para um segundo catálogo. Os nomes/caminhos CLI ficam na projeção; uso de modelo, estado ocupado e mutação devem vir da política compartilhada.
2. **Um worker de execução.** Não empilhar `InteractiveExecutionController` sobre o executor de `WorkbenchSession`, criando dois donos do cancelamento e settlement. O novo adaptador WebView2 deve consumir o controller comum. A sessão Tk atual pode permanecer intacta durante a migração.
3. **Gerações distintas.** Geração da sessão/workspace, geração da execução, query generation, run id canônico e geração de abertura de artefato não são a mesma identidade. As correções de terminal antes do accepted e de leitura A/B continuam necessárias na fronteira assíncrona da GUI.
4. **Mailbox não é resultado canônico.** A mailbox atual limita/coalesce projeções, substitui o último erro de apresentação e mantém uma quantidade limitada de terminais. O controller tem outro canal para resultados. Reutilizar a mailbox como única fila da bridge perderia garantias das correções pré-piloto. Settlement, cancelamento e erros essenciais precisam de entrega ordenada, correlação e backpressure próprios.
5. **Limites precisam de compatibilidade.** O CLI tem pendências limitadas a 16 itens, 8.192 caracteres por item e 131.072 no total; o WebView2 aceita entrada de 16.000 caracteres. A extração não pode reduzir um limite silenciosamente. Os limites devem ser explícitos por adaptador e cobertos por testes.
6. **Consultas locais independem do modelo.** Arquivos/leitura/busca não devem exigir uma geração nem falhar porque o endpoint está fora do ar. Separar a criação dos serviços de workspace da inicialização do Agent é parte da composição desejada.
7. **Seleção recria a sessão.** O CLI valida/persiste a seleção de modelo e sinaliza rebootstrap, com checagens de idle. Troca de workspace também verifica execução, consultas e atenção pendente. A GUI deve respeitar a mesma barreira, não apenas mudar um rótulo ou URL.
8. **Aprovação não vira permissão implícita.** O broker é reutilizável; os botões de aprovar/negar exigem revisão da identidade, argumentos e diff atuais. Enquanto essa tela não existir, preservar UNAVAILABLE/READ_ONLY do Workbench.
9. **Imports compatíveis e testes de arquitetura.** Manter fachadas/reexports nos caminhos CLI antigos; testar identidade dos tipos, construtores usados e comportamento. Ajustar os verificadores de arquitetura para reconhecer o owner extraído sem relaxar confinamento, execução em background ou limites.

## Sequência recomendada

1. **Extrair coordenação com compatibilidade.** Mover os componentes de baixa dependência para o pacote comum, mantendo os imports CLI existentes como fachadas. A primeira entrega deve preservar o comportamento do CLI, sem migrar a GUI ao mesmo tempo.
2. **Separar admissão/composição de renderização.** Centralizar políticas de ação, composição de serviços, executor da operação e encerramento ordenado. O terminal continua sendo um consumidor desse núcleo. Não capturar ANSI/stdout de handlers para fingir uma API estruturada.
3. **Substituir a coordenação paralela do WebView2.** Criar seu adaptador de sessão sobre o núcleo comum, preservando a bridge estreita e as proteções atuais. Conectar primeiro comandos locais, consultas/Arquivos, inventário/Ferramentas e snapshot/Tarefas. São ganhos concretos com owners já existentes.
4. **Trazer os fluxos completos.** Seleção de workspace/perfil e rebootstrap, first-run, histórico/memória, paleta e aprovação visual, cada qual usando o use case existente e sua política. Não apresentar o conjunto como paritário antes desses fluxos e testes.

O primeiro marco verificável é: **o CLI executa a mesma bateria usando o núcleo extraído; o WebView2 passa a usar esse mesmo núcleo em uma entrega seguinte**. Isso evita uma terceira implementação concorrente de estado ou um framework genérico sem consumidor real.

## Experimentos e testes desta avaliação

- Inventário de 46 bindings e dependências de 11 módulos: `evidence/cli-reuse-20261022/reuse-inventory.json`.
- Prova em processo novo: o catálogo, listagem, leitura e busca funcionaram com imports de CLI e providers de modelo explicitamente proibidos. Script e resultado: `probe_neutral_reuse.py` e `neutral-probe-result.json`, na mesma pasta. Não se trata de frontend novo, apenas de verificação de reaproveitamento dos serviços atuais.
- Bateria existente de controller, UI plane, query plane, action registry e paridade de consultas: **30 passaram e 1 foi pulado**. O único caso com skip depende de criação de symlink indisponível no host. Log e resultado: `cli-tests-ordinary-user.log` e `cli-tests-ordinary-user-result.json`.
- Houve tentativas iniciais com erro de preparação de diretório e depois restrição de acesso às temporárias do pytest no sandbox. Os registros foram preservados. A bateria concluída rodou fora do sandbox com token **não administrador**, conferido antes da execução; não foram alteradas permissões.

Essa validação cobre os componentes selecionados, não toda a CLI nem uma migração que ainda não foi feita. Não houve uso de endpoint real, carregamento de Qwen, commit, merge ou publicação.
