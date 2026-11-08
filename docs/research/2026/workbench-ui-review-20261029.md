# Segunda revisão do Workbench — 29/10/2026

A revisão confirmou a cobertura das 46 ações do catálogo interativo compartilhado
e encontrou problemas adicionais de interface. Os três controles que eram
placeholders agora têm ações; os 25 botões estáticos têm handlers verificados.
Recursos sem configuração, sem permissão ou ocupados continuam explicitamente
indisponíveis enquanto essa condição existir.

## Controles

| Controles | Comportamento |
| --- | --- |
| Recolher navegação | Recolhe e permite expandir novamente; atualiza o rótulo e `aria-expanded`. |
| Chat, Arquivos, Tarefas e Ferramentas | Navegam para conversa, consultas, histórico e inventário compartilhados. Tarefas permite acompanhar o estado durante uma execução. |
| Atualizar artefatos e Relatórios e outputs | Consultam outputs retidos, preservando a seleção mais recente contra respostas atrasadas. |
| Perfil | Abre os comandos canônicos de modelo, workspace e modo, com filtro e preparação de rascunho. |
| Testar conexão | Usa o teste manual existente do catálogo, sem inferência. |
| Tema e Atalhos | Alternam claro/escuro e apresentam os atalhos disponíveis. |
| Explorar este projeto | Lista o workspace atual; não pressupõe a existência de `notes.txt` nem chama um modelo. |
| Ver diff, Negar e Aprovar | Mostram o diff disponível e resolvem a identidade de aprovação pelo broker. |
| Comandos | Abre a paleta gerada pelo catálogo compartilhado. |
| Adicionar arquivo e Usar no chat | Abrem a consulta de arquivos e acrescentam ao rascunho o caminho de um texto lido com sucesso. Não fazem upload nem enviam automaticamente. |
| Parar e Enviar | Solicitam cancelamento e submetem tarefas/controles/mensagens pendentes pelos owners existentes. |
| Fechar painel | Fecha a inspeção e devolve o foco à conversa. |
| Quebra de linhas | Alterna a quebra de linhas dos textos do painel. |
| Consultar e Atualizar tarefas | Executam consultas e atualizam o histórico pelos serviços existentes. |
| Copiar conteúdo | Copia o conteúdo carregado; dispõe de fallback local quando a API de clipboard não está disponível. |

Os botões gerados para arquivos, artefatos, execuções, paginação e comandos
continuam usando os mesmos contratos. A escolha de duas execuções em sequência
mantém os detalhes da seleção mais recente, mesmo que a resposta anterior chegue
depois. Respostas antigas de consultas e outputs também não substituem outro
painel escolhido pelo usuário. Rascunhos novos são preservados diante de rejeições
ou respostas atrasadas.

## Apresentação e custo

As mudanças usam apenas HTML, CSS e JavaScript locais. Não há framework, fonte
remota, biblioteca de ícones ou dependência adicional. O host pywebview conserva
seu servidor local transitório para servir os assets; isso não inicia um backend
de modelo nem um serviço persistente.

Foram ajustados contraste no tema claro, legibilidade da conversa, foco de teclado,
tamanho dos pequenos controles e indicação da navegação ativa. A regra `hidden`
agora prevalece sobre os layouts em grid. A navegação recolhida conserva um
controle para reabertura em janelas largas; em janelas estreitas, usa o layout
compacto existente.

## Evidência e limite

- `workbench_buttons_ui.cjs`: verifica os 25 handlers e exercita perfil, referência
  de arquivo, workspace obsoleto, quebra de linhas, recolher/expandir, tema,
  atalhos, clipboard e disponibilidade.
- `workbench_panels_ui.cjs`: verifica estados indisponíveis e respostas atrasadas
  de outputs e consultas.
- `workbench_history_ui.cjs`: verifica paginação, duplicação, atualização e a corrida
  entre seleções de detalhes.
- `workbench_commands_ui.cjs`: verifica comandos, aprovações, fila, rascunhos e saída.

Os quatro harnesses foram executados pelo Node REPL, independentemente da ausência
de Node no PATH do pytest. A regressão Python terminou com 214 testes aprovados e
quatro ignorados (os harnesses Node), além de um aviso de finalização de variável
Tk na suíte legada. Mypy passou em 962 arquivos. Os testes usam gateways offline e
não fizeram inferência real.

A janela de teste usou workspace e configuração isolados. O usuário interrompeu
Computer Use com Esc; a inspeção visual nativa não foi concluída e não é
apresentada como aprovada. As alterações estão no código e no wheel atualizado.
O atalho instalado anterior não é substituído por esta revisão.
