# Investigação: generalização de capacidades da CLI

**Data:** 2026-10-22
**Branch:** `feat/agent-workbench`
**HEAD observado:** `9b5a2ddc2645430142e3c1ecaf6ef95c41e40406`
**Tipo de trabalho:** investigação somente de leitura; este relatório não implementa extrações.

## Pergunta e conclusão

É possível permitir que outras aplicações reutilizem mais capacidades hoje acessadas pela CLI. A base já existe: casos de uso em `application`, contratos de ações em `actions`, coordenação compartilhada em `interactive` e uma composição WebView2 que consome a sessão compartilhada.

A reutilização ainda é parcial. A CLI continua sendo a implementação de referência de várias operações interativas, e o catálogo localizado em `interactive` contém projeção e referências de handlers específicos da CLI. A sessão compartilhada aceita apenas uma parte das ações catalogadas. Assim, importar um único serviço ainda não dá a outra aplicação o mesmo conjunto de capacidades da CLI.

O caminho recomendado é extrair operações e contratos neutros de interface, mantendo terminal, shell, sintaxe e renderização como adaptadores da CLI. O maior ganho inicial viria de separar o registro de ações compartilhado das associações e handlers CLI, seguido por extrações pequenas de operações que hoje misturam regra de aplicação com entrada/saída de terminal.

## Escopo e limites da evidência

A inspeção cobriu `src/llm_agent/actions`, `application`, `interactive`, `interfaces/cli`, `interfaces/workbench` e os documentos F1 relevantes. A árvore de trabalho já contém alterações acumuladas da F1 e artefatos não rastreados; o HEAD permaneceu no baseline indicado acima. Este relatório descreve o checkout atual, inclusive seu estado não consolidado, e não atribui as diferenças locais a uma única rodada.

Não foram alterados arquivos de produção, políticas, baselines ou evidências. Não foram executados testes nesta investigação. As conclusões abaixo são inspeção estática do código e dos contratos visíveis; a proposta de arquitetura ainda precisa de validação com os owners e gates do repositório antes de virar implementação.

## Mapa atual

| Camada | O que já oferece | Limite observado |
|---|---|---|
| `llm_agent.actions` | Catálogo imutável e definições de ações independentes da interface. | Não é, sozinho, um executor nem define apresentação para cada UI. |
| `llm_agent.application` | Casos de uso e contratos para inicialização/execução, configuração, aprovação, consultas ao workspace, continuidade, extensões e outros serviços. | Algumas operações de interação e suas decisões de fluxo ainda são compostas pelos módulos CLI. |
| `llm_agent.interactive` | Sessão, admission, controller, cancelamento, mailbox, projeções e sintaxe de consulta compartilhados. | A sessão só encaminha um subconjunto de ações; o registro em `interactive/commands.py` contém conceitos e destinos CLI. |
| `llm_agent.interfaces.cli` | Parser de argumentos, shell, comandos, renderização, seletores, prompts, completion e composição de terminal. | Ainda é owner de comportamentos que outra interface precisaria consumir sem Rich, stdin/TTY ou prompt síncrono. |
| `llm_agent.interfaces.workbench` | Bridge WebView2 sobre `InteractiveSession`; usa a sessão compartilhada para execução e os serviços existentes para consultas e painéis. | A bridge expõe a superfície da UI, não transforma automaticamente todo comando CLI em uma capacidade compartilhada. |

### Evidência de reutilização já existente

- `application/task_execution.py` oferece `TaskExecutionStart`, `start_interactive_session`, `execute_submission`, leitura de contexto/capacidades, cancelamento e fechamento como operações da camada de aplicação.
- `application/services/queries.py` oferece contratos tipados e um serviço de consultas somente de leitura para listar, ler, buscar e consultar Git no workspace.
- `interactive/session.py` declara-se uma facade independente de UI e compõe `TaskExecutionRuntime`, consultas, disponibilidade, controller e settlement.
- `interfaces/workbench/webview.py:create_session` constrói `InteractiveSession`; a `WorkbenchBridge` consome essa sessão, em vez de iniciar uma execução paralela própria.
- `actions/catalog.py` contém `ActionCatalog`, separado de widgets e terminal.

Esses elementos demonstram que uma nova aplicação já pode reutilizar capacidades centrais sem invocar a CLI. Também mostram que o caminho de integração ainda exige conhecimento de vários módulos e dos detalhes de composição.

### Lacunas observadas

1. `interactive/commands.py` se descreve como projeção CLI e define `CliActionBinding`, `CliActionRegistry` e `DEFAULT_CLI_ACTION_REGISTRY`, apesar de residir na camada compartilhada. Os bindings armazenam owners como strings sob `llm_agent.interfaces.cli.*`. A ligação é indireta, mas ainda cria dependência conceitual do núcleo para handlers de terminal.
2. `interactive/session.py:submit` trata consultas locais, inspeções, ajuda e submissões naturais/agentic suportadas; para outras ações do catálogo retorna `ACTION_UNAVAILABLE`. Portanto, catálogo completo não equivale a implementação completa na sessão.
3. A composição CLI em `interfaces/cli/bootstrap.py` cria `CommandContext` e conecta shell, controller, mailbox, broker de aprovação e serviços. Esse contexto é flexível, porém combina vários colaboradores da interface em um objeto amplo; não é um contrato neutro recomendado para outra aplicação.
4. Handlers em `command_handlers.py`, `interactive_commands.py`, `memory_commands.py` e `task_commands.py` frequentemente recebem texto bruto e um contexto genérico, e alguns chamam diretamente prompt, console, shell ou seletor. O comportamento não pode ser reutilizado com segurança apenas importando o handler.
5. Recursos CLI como `InteractiveShell`, `TerminalSelector`, parser de argumentos do executável, completion de shell e renderização Rich são adequadamente específicos do terminal. Eles não deveriam ser movidos para `application` só para aumentar contagem de código compartilhado.

## O que generalizar e o que manter na CLI

### Candidatos a contratos/casos de uso compartilhados

- **Descoberta de capacidades:** catálogo de ações disponíveis na sessão, estado (`available`, `busy`, `not_implemented`, `not_configured`, etc.), política de admissão e motivo acessível. Deve representar a implementação real da aplicação e não apenas todas as definições do catálogo.
- **Operações de sessão:** submeter pedido tipado, cancelar por identidade, acompanhar eventos e consumir settlement por contrato estável. A `InteractiveSession` já é um início; a API pública precisa evitar que adapters dependam de atributos privados ou de strings de handler CLI.
- **Operações locais de workspace:** continuar baseadas no serviço de consultas existente e em requests/results tipados. A CLI fica responsável por converter linha de comando em request e apresentar o resultado.
- **Operações de tarefas, continuidade e relatórios:** expor casos de uso existentes da camada application com projeções primitivas tipadas. A interface escolhe se mostra tabela, painel, texto ou notificações.
- **Alterações de configuração e memória:** extrair a decisão e validação para use cases quando ainda estiverem concentradas em handlers. Qualquer entrada interativa deve vir de um port/callback injetado; nunca embutir `console.input` no serviço compartilhado.
- **Aprovação e seleção:** manter contratos de pedido/resposta neutros e injetar um presenter/resolver. Uma CLI pode pedir confirmação em texto; uma GUI pode mostrar diálogo; um serviço headless pode responder `UNAVAILABLE` ou aplicar política própria explícita.

### Responsabilidades que devem ficar como adaptadores CLI

Parser de argumentos de processo, aliases e sintaxe de comandos, prompt e edição de linha, atalhos de teclado, seletor de terminal, completion PowerShell/Bash, cores/Rich, impressão de streaming e códigos/formatos próprios do executável. Esses adaptadores traduzem interação humana para contratos application e apresentam seus resultados.

## Opções para a decisão

| Opção | Resultado | Custo/risco | Avaliação |
|---|---|---|---|
| Reutilizar handlers CLI diretamente | Outra UI importa `command_handlers` e fornece um `ctx` parecido. | Herda console, texto cru, objetos genéricos, prompts e comportamento implícito. Tendência a duplicar contexto e quebrar sem aviso. | Não recomendado como API geral. Útil só para compatibilidade interna CLI. |
| Extrair toda a CLI de uma vez | Migrar todos os comandos e o shell para uma camada comum. | Escopo amplo; mistura apresentação com domínio e arrisca regressões CLI/Tk/Workbench. Difícil auditar por fatias. | Não recomendado. |
| Extrair contratos e casos de uso por fatias | Desacoplar binding/registro; mover a lógica reutilizável para application; cada UI injeta apresentação. | Requer desenhar contratos e manter adapters durante migração. Mudanças pequenas podem ser cobertas com regressão da CLI e de outra interface. | Recomendado. |

## Plano incremental proposto

1. **Estabelecer a fronteira do catálogo.** Separar definições neutras de ação das associações de caminho, alias, completion e handler CLI. O registro CLI pode continuar sendo a projeção que conhece `/comandos`; a sessão compartilhada deve receber capacidades/dispatchers apropriados, em vez de importar a projeção CLI ou percorrer `_bindings` privado.
2. **Tornar as capacidades implementadas explícitas.** Criar uma projeção tipada de capacidades da sessão ligada aos adapters realmente instalados. Usar a mesma fonte para ajuda/comandos da CLI e botões/ações de outra UI, sem anunciar como disponível algo que será recusado depois.
3. **Extrair operações pequenas dos handlers.** Para cada grupo (perfil/configuração, memória/histórico, tarefas/continuidade, inspeção), identificar a regra independente do terminal, criar request/result tipados no owner application apropriado e deixar o handler CLI traduzir entrada e renderizar resultado. Não mover uma função inteira quando ela mistura os dois lados.
4. **Injetar interação por ports.** Pedidos de aprovação, escolha de perfil/workspace e confirmação devem usar interfaces mínimas de callback/protocolo. A ausência de UI precisa ter resultado definido e não pode fazer uma operação silenciosamente permissiva.
5. **Validar por consumidor.** Para cada fatia, manter testes CLI existentes e acrescentar um consumidor pequeno sem terminal, além de testar limites de permissão, cancelamento e indisponibilidade. Atualizar arquitetura/ownership somente depois de justificar a nova direção e obter resultado dos gates, nunca apenas para torná-los verdes.

## Critérios para decidir que uma capacidade ficou realmente geral

Uma capacidade está pronta para outras aplicações quando:

- possui request/result ou protocolo público tipado e documentado;
- não requer `CommandContext`, Rich, TTY, prompt CLI ou import de `interfaces.cli`;
- tem ownership definido em `application` ou módulo neutro apropriado;
- declara disponibilidade e limites de permissão antes da chamada;
- comporta cancelamento, falha e resultado terminal sem depender de detalhes privados;
- pode ser exercitada por um consumidor não CLI nos testes;
- a CLI usa o mesmo caso de uso, em vez de manter uma segunda implementação.

## Riscos e cuidados

- **Aumentar a API pública cedo demais:** estabilizar nomes antes de entender os consumers pode criar contratos difíceis de evoluir. Começar pelos requests/results usados por dois adapters reais.
- **Anunciar mais do que está implementado:** o catálogo de ações é mais amplo do que o subset executável pela sessão. Disponibilidade deve vir da composição concreta de cada app.
- **Misturar policy com interface:** aprovações, modos de execução, READ_ONLY e validação de segurança permanecem nos owners canônicos. A camada visual não ganha permissão por oferecer um botão.
- **Criar dois caminhos de execução:** CLI, Workbench e aplicações futuras devem compartilhar controller/runtime quando a semântica for a mesma; não criar outro scheduler ou armazenamento de execuções.
- **Perder compatibilidade CLI:** handlers públicos e aliases usados por testes/integrações podem exigir wrappers temporários enquanto os use cases são extraídos.

## Recomendação para o chat decidir

Autorizar uma primeira fatia restrita à fronteira de ações: tornar neutro o contrato de registro/capacidade consumido por `InteractiveSession`, mover os bindings e aliases de terminal para a composição CLI e remover o uso de `_bindings` privado. Nessa primeira fatia, não migrar todos os handlers nem redesenhar a GUI.

Depois dessa mudança, usar dois casos reais para validar a direção: uma consulta local e uma operação de sessão que hoje só a CLI expõe. Escolher o segundo caso após mapear seus efeitos e confirmar o owner application apropriado. Só então decidir quais grupos de comandos valem extração adicional.

Essa abordagem reduz o acoplamento estrutural demonstrado pelo código sem transformar a extração da CLI em um projeto amplo, e deixa a decisão de expansão apoiada por um consumidor não terminal verificável.

## Referências locais examinadas

- `src/llm_agent/actions/catalog.py`
- `src/llm_agent/application/task_execution.py`
- `src/llm_agent/application/services/queries.py`
- `src/llm_agent/interactive/session.py`
- `src/llm_agent/interactive/commands.py`
- `src/llm_agent/interfaces/cli/bootstrap.py`
- `src/llm_agent/interfaces/cli/command_handlers.py`
- `src/llm_agent/interfaces/cli/interactive_commands.py`
- `src/llm_agent/interfaces/cli/selector.py`
- `src/llm_agent/interfaces/cli/interactive_shell.py`
- `src/llm_agent/interfaces/workbench/webview.py`
