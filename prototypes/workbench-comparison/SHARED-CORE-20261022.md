# Núcleo interativo compartilhado — implementação incremental

A coordenação antes localizada no CLI foi extraída para `src/llm_agent/interactive`. O CLI mantém fachadas de compatibilidade e sua composição de terminal; o WebView2 passa a consumir `InteractiveSession`, sem depender do adaptador Tk.

## Reuso efetivo

- Controller e gerações, settlement, cancelamento, pendências limitadas e transporte de streaming.
- Broker de atenção, mailbox e projeção de estado; renderização de toolbar continua no terminal.
- Registro/parser de comandos, construção de envelopes, interpretação de argumentos de consultas e formatação de resultados.
- Executor limitado de consultas; a apresentação especial de consultas durante execução continua no adaptador CLI.

A sessão gráfica usa os casos de uso canônicos existentes: inicialização configurada, submissão, cancelamento, consultas, inventário e leitura de artefatos. Não adiciona um segundo worker de execução. Arquivos consulta o workspace, Tarefas mostra o estado da sessão e Ferramentas consulta o inventário. Arquivos e Tarefas não inicializam o modelo nem exigem configuração. Ferramentas exige runtime configurável, mas não gera uma resposta de modelo.

Entradas naturais, `/agent` e retry usam a execução canônica. Consultas locais reconhecidas usam serviços de consulta; `/help` e `/commands` mostram o subconjunto integrado. Comandos desconhecidos ou conhecidos sem adaptador gráfico são recusados explicitamente, sem virar prompt. Os atalhos de estado mostram o snapshot atual; não constituem uma interface completa de timeline histórica.

## Limites desta entrega

Não há paridade integral com os 46 comandos do CLI. Seletores de modelo/workspace, configuração inicial, histórico, memória e revisão gráfica de aprovações continuam exigindo adaptadores. A sessão gráfica permanece READ_ONLY e declara aprovação indisponível. A fachada gráfica serializa operações; as capacidades de consultas concorrentes do executor compartilhado não são anunciadas como disponíveis na GUI.

Os cinco botões ainda sem recurso integrado ficam desabilitados com motivo. Os três novos botões de inspeção também ficam desabilitados durante execução e quando a bridge não anuncia suporte. A disponibilidade do endpoint continua explicitamente não verificada até sua utilização; nenhum modelo foi carregado nesta validação.

## Validação

- **222 testes Python passaram; 1 skip de symlink.** Abrange controller, atenção, query plane, parser/registro/completion, compatibilidade CLI, cancelamento, renderização, contratos Tk, paridade de consultas, adaptador compartilhado e bridge/configuração WebView2.
- Frontend: **17 cenários de submissão + 12 cenários de painel/cancelamento/proveniência/streaming** passaram. Auditoria dos **18 botões**: 13 ações exercitadas, 5 indisponíveis identificados; nenhum botão habilitado sem ação; oferta dinâmica de relatório exercitada.
- Novo teste distingue timeout terminal do provedor de timeout de espera: o primeiro conclui a Future e permite outra execução; o segundo mantém a operação coletável. Sem sleeps para coordenar esses testes.
- Ruff, compilação e `W17_ARCHITECTURE=PASS`. O guard verifica as implementações extraídas, além das fachadas; teste de mutação garante que um worker daemon indevido seja detectado.
- Integração canônica com gateway determinístico injetado somente no teste: relatório, identidade, bytes e inventário reais da aplicação, sem endpoint externo.

Logs e argumentos: `evidence/shared-core-20261022/final-tests.*`, `final-js-tests.json`, `final-quality.json` e respectivos logs. As baselines pré-extração estão no mesmo diretório. Evidências anteriores, inclusive a rodada 28 bloqueada, foram preservadas; nenhuma causa nova foi atribuída.

## Host e inspeção manual

Smoke de abertura/fechamento normal, sem prompt e sem modelo: `evidence/endpoints-20261022/20261022T201242156435Z`. Host e sete subprocessos observados encerraram com código 0; `host-result.json` registra retorno do WebView e fechamento da sessão. Stderr foi preservado. Chromium 1411 não apareceu nesta execução; isso não explica nem invalida ocorrências anteriores. A amostragem de processos é de 50 ms e pode perder filhos muito breves.

1. Execute `.venv/Scripts/python.exe prototypes/workbench-comparison/run_workbench.py --workspace <pasta>`; opcionalmente informe os mesmos `--home`, `--config` e `--profile` usados no CLI.
2. Abra Arquivos e Tarefas antes de enviar uma solicitação. Confira conteúdo e origem, sem precisar de modelo; use `/ls`, `/read <arquivo>` e `/find <texto>` para conferir as consultas locais.
3. Abra Ferramentas com uma configuração válida. Se faltar configuração, confira erro explícito; comandos não integrados devem mostrar indisponibilidade, sem submissão ao modelo.
4. Para cancelamento sem modelo, execute o launcher de inspeção com `--offline-fixture`. Inicie a tarefa, aguarde chunks, pare, aguarde settlement e repita. Confira também relatório, cópia, fechamento/reabertura e identidade do conteúdo. Este modo é explicitamente de teste e não implementa as três inspeções reais.
5. Para avaliar execução real, use uma configuração já disponível e um objetivo explícito; confira stream e relatório. O launcher não inicia uma campanha automaticamente. Feche normalmente e examine os novos logs de host/stderr/processos.

**Aceitação visual pendente da inspeção do usuário.** Tk, camada application e contratos canônicos não foram alterados nesta extração. Não houve commit, merge, push, alteração de permissões ou carregamento de Qwen.
