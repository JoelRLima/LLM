# Baterias de testes no Workbench — 02/11/2026

O painel **Testes** integra importação, conferência, execução, acompanhamento, controles e evidências à aplicação. O menu/CLI permanece disponível e usa o mesmo núcleo.

## Implementação e fronteiras

- TestQueueLibrary é o caso de uso compartilhado para criação de filas gerenciadas, abertura de filas externas e oito seleções recentes.
- SessionTestQueues, no pacote interactive, usa TestQueueService e o controlador interativo existente. A execução ocupa o mesmo owner da sessão; não pode competir com outra execução do chat.
- A bridge transporta pedidos e oferece o seletor nativo de diretórios. Não constrói runtime nem classifica resultados.
- O JavaScript apresenta as observações e captura escolhas; atualização, filtros e paginação pertencem à apresentação.
- Cancelamento e pausa recebem sinais no agendador existente. Isso inclui o intervalo anterior à primeira tarefa. Filas de outro processo usam os controles persistidos do mesmo núcleo.
- Uma identidade opcional esperada em QueueExecutionRequest vincula configuração/código conferidos à execução. A divergência é rejeitada antes de construir o executor.
- As opções visíveis da execução ficam registradas no progresso, sem persistir autorização para iniciar novas chamadas. A identidade continua sendo validada pelo núcleo.
- Leitura de arquivos de evidência e publicação de HTML continuam na aplicação; os renderizadores permanecem nas interfaces.

## Melhorias de experiência adicionais

Seleção de pastas, filas recentes e destino automático reduzem digitação. Há abertura por caminho quando o seletor não pode ser usado. Cancelar o seletor preserva a entrada.

A preparação tem guia de estrutura e erros identificam nomes de pasta inválidos. A conferência destaca ausência de critérios, limites e compatibilidade. Aprovação de chamadas de modelo e aprovação automática de ferramentas são escolhas separadas e visíveis.

Fechar o painel não para a execução. O painel pode ser reaberto e mantém o rascunho do chat. Os controles de execução são protegidos contra clique repetido, respostas tardias e troca de projeto/fila.

Resultados que precisam de atenção são destacados. O usuário pode mudar o filtro e mostrar mais tarefas. Há resumo legível e dados técnicos expansíveis, sem transformar texto do modelo em HTML executável. O caminho do relatório permanece visível depois das atualizações.

O diálogo usa os controles nativos de foco e Escape, mantém o fechamento acessível ao rolar e evita atualizar o anúncio de status com texto idêntico a cada consulta. Como referências foram consultadas as orientações do [W3C para diálogos modais](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/) e para [mensagens de status](https://www.w3.org/WAI/WCAG21/Understanding/status-messages.html). Isso não constitui certificação ou auditoria completa WCAG.

## Evidências

- Regressão: 759 aprovados, 14 ignorados.
- Testes direcionados de fila, serviço, UX e runtime: 58 aprovados.
- O caminho completo bridge → sessão → serviço → subprocesso → runtime canônico foi exercitado com respostas de modelo simuladas e verificação do artefato real.
- Testes cobrem pausa anterior à primeira tarefa, retomada sem repetição, cancelamento, fila de outro caller, preservação das pendências do chat, falta de autorização e configuração alterada após conferência.
- O harness Node de botões passou.
- Edge headless aprovou três visualizações da nova tela e três dos fluxos anteriores de recuperação/fila.
- Ruff passou; mypy passou nos sete módulos verificados.
- O checker arquitetural passou sem flexibilizar regras. Nenhuma inferência real foi usada.
- A seleção nativa é simulada nos testes automatizados; sua chamada segue a API da versão instalada de pywebview.

[Regressão](../../../../reports/workbench-batteries-20261102/regression.xml), [prova do runtime](../../../../reports/workbench-batteries-20261102/runtime.xml), [testes direcionados](../../../../reports/workbench-batteries-20261102/final.xml), [nova tela](../../../../reports/workbench-batteries-20261102/browser) e [fluxos anteriores](../../../../reports/workbench-batteries-20261102/existing-flows).

## Limites e próximas prioridades

A execução continua exigindo o processo responsável aberto. Pausa por limite de caso e retomada das pendentes não equivalem a congelamento de ferramentas ou LLM, nem a desfazer efeitos externos. Os projetos separados não oferecem sandbox.

Projetos iniciais ainda são fixtures UTF-8 dentro dos limites de importação existentes. Expansão para projetos maiores/binários e comparação visual entre experimentos são prioridades posteriores. Não foi adicionado ciclo automático de reparo nem habilitação de plugins na bateria a partir das permissões temporárias do chat.
