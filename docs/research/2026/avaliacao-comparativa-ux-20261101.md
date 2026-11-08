# Avaliação comparativa de UX — 2026-11-01

## Objetivo e limites

Esta avaliação combina relatos públicos individuais com padrões documentados em
produtos de agentes de código. Os relatos apontam riscos a verificar no próprio
Workbench; não demonstram frequência, causa comum ou defeito em todos os usuários.
O estado abaixo é o visto nas páginas em 2026-11-01. A correspondência local é
uma inspeção estática do código atual, salvo quando indicada como teste; não
afirma que reproduzimos os incidentes de terceiros.

## Matriz de relatos públicos

| Fonte e estado visto | Sintoma relatado e data | Limite da evidência | Correspondência local, prioridade e proposta de verificação |
| --- | --- | --- | --- |
| [Codex #8263](https://github.com/openai/codex/issues/8263) — fechada; aberta em 18/12/2025, encerrada em 23/12/2025; pedido posterior de reabertura em 08/02/2026 | Uma pessoa relata que ↑ substituiu um rascunho longo pelo histórico e que ↓ não recuperou o texto. | O próprio autor reconheceu dificuldade em montar uma reprodução estável; relato isolado, sem prevalência estabelecida. | **Correspondência local, P1:** selecionar comando na paleta substitui diretamente o texto atual do compositor. Remediar mantendo rascunho anterior identificável por workspace e permitindo restauração explícita. Testar texto multilinha, troca de workspace, limites de tamanho e que restaurar nunca envia. |
| [Codex Windows #45404](https://github.com/openai/codex/issues/45404) — aberta em 14/09/2026; atualização de escopo em 22/09/2026 | A pessoa relata que o segundo envio fica parado como rascunho durante uma tarefa e depois de sua conclusão. | Relato de uma configuração Windows; a causa exata não está confirmada. | **Caso de regressão adjacente, não defeito local confirmado:** o Workbench separa `enqueue()` durante tarefa de `submit()` quando ocioso. Testar ambas as transições, settlement e respostas de bridge atrasadas para garantir que o texto e seu estado visível não se percam. Não enviar automaticamente. |
| [Claude Code #826](https://github.com/anthropics/claude-code/issues/826) — aberta em 19/04/2025 | Relato macOS descreve saltos e cintilação para o topo ao digitar ou receber texto, em histórico longo. | Um terminal/versão relatados; não mede o alcance em outras plataformas. | **Correspondência local, P2:** `message()` e o stream atualizam `scrollTop` para o fim sempre. Respeitar quando a pessoa está lendo acima do fim e oferecer **Ir para o fim**. Testar leitura manual durante novos deltas e retomada do follow quando já está no fim. |
| [Claude Code #28086](https://github.com/anthropics/claude-code/issues/28086) — duplicata fechada em 27/02/2026 e bloqueada em 09/03/2026 | A pessoa relata pedidos de permissão repetidos quando uma regra casa com subcomando dentro de pipeline; também relata gravação de sintaxe que não casa. | Fechar como duplicata encaminha a outra issue; não demonstra que a experiência foi corrigida. | **Não é uma falha correspondente demonstrada no Workbench.** Manter escopo de workspace, authority e aprovação explícita; não relaxar permissões para reduzir atrito. Testar aprovações em comandos compostos apenas quando esses fluxos forem alterados. |
| [Gemini CLI #21096](https://github.com/google-gemini/gemini-cli/issues/21096) — aberta em 04/03/2026, encerrada como concluída em 02/04/2026 | Relato em Termux/Android descreve indicador de carregamento persistente depois do cancelamento; o histórico aponta para mudança de recuperação do estado de retry. | O status concluído pertence a essa issue e alteração; não prova o comportamento de outras versões ou ferramentas. | **Correspondência local, P1:** distinguir estado transitório, cancelamento e resultado terminal; conservar qualquer observação parcial que o owner tenha entregue junto do erro. Testar cancelamento, erro terminal com settlement e atraso entre eventos. Não apresentar erro como sucesso nem repetir a execução automaticamente. |

Os itens locais são achados de leitura do código, não reproduções dos relatos
públicos. Na auditoria inicial, o compositor perdia rascunhos, o scroll seguia
sempre o fim, o painel mostrava JSON cru como resumo e erros podiam ocultar um
settlement parcial. O worktree atual tem recuperação temporária de rascunhos,
preservação da posição com **Ir para o fim**, resumo humano com JSON sob demanda e
apresentação de observação/relatório parcial em um caminho de erro. Ícones de
eventos agora usam severidade explícita. Os cinco harnesses JS passaram no gate
final; cobrem rejeição/carregamento de Tarefas e resposta antiga após atualização
mais recente no mesmo painel, preservando referências com `run_id` nulo. A revisão visual encontrou e corrigiu
uma regra CSS que não aplicava ao resumo de Tarefas e uma sobreposição do botão
**Ir para o fim** sobre o composer em 900×650. Nas capturas revisadas, o resumo
está estilizado e o botão fica no fluxo sem sobreposição em leitura a 900×650 e
1280×800; **Recuperar rascunho** cabe em 900×650. São cenários sintéticos em Edge
headless com bridge falsa, não validação de janela nativa, bridge real ou fluxo
completo.

## Padrões úteis documentados por outros produtos

| Padrão observado na documentação | Aplicação possível ao Workbench | Limite que devemos manter |
| --- | --- | --- |
| [Claude Code — modo interativo](https://code.claude.com/docs/en/interactive-mode) documenta recuperar texto limpo do histórico e visualizar mensagens enfileiradas enquanto o agente trabalha. | Rascunho recuperável e ação explícita de voltar a acompanhar o fim tornam a interação previsível. O follow-up deve continuar visivelmente em fila/rascunho até o usuário o enviar. | Não copiar a política de fila ou atalhos sem compatibilidade local; mensagem em fila não deve começar sozinha ao encerrar a tarefa. |
| [Claude Code — checkpoints](https://code.claude.com/docs/en/checkpointing) descreve snapshots por prompt e ações separadas para restaurar código, conversa ou resumir. A própria documentação delimita arquivos não rastreados e retenção. | Deixar claro o escopo da recuperação e o que ela altera. | Rascunhos do Workbench pedem recuperação leve e temporária; não adicionar clone de checkpoints ou promessa de desfazer alterações. |
| [ChatGPT — Code Review](https://learn.chatgpt.com/docs/code-review?surface=app) mostra mudanças no diff e orienta confrontar achados com linhas e testes. | Relatar estado e completude com acesso ao artefato/diff que sustenta a afirmação; separar observação parcial de sucesso. | Uma review do diff não equivale a validar a tarefa, executar testes ou aprovar a alteração. |
| [Gemini CLI — planejamento com todos](https://geminicli.com/docs/cli/tutorials/task-planning/) apresenta uma lista de etapas com foco atual, concluídas e pendentes, que o usuário pode revisar e ajustar. | Expor resumo humano do progresso com dados existentes e deixar o detalhe técnico disponível sob demanda. | Não inventar percentuais, progresso de modelo ou etapas que o runtime não reporta. |
| [Aider — integração Git](https://aider.chat/docs/git.html) documenta diff e desfazer/revisar mudanças; também registra o comportamento próprio de commits automáticos. | Tornar alterações e diferenças visíveis, preservando a possibilidade de revisar antes de decidir. | Não adotar auto-commit, undo ou qualquer operação sobre mudanças preexistentes; este checkout preserva estado dirty. |
| [OpenCode v2 — permissões](https://opencode.ai/v2/docs/permissions) separa decisões `allow`, `ask` e `deny`, e documenta regras de escopo e políticas hard-deny. | Explicar claramente qual recurso e qual decisão são solicitados ao usuário. | A semântica e os schemas de outro runtime não se transferem; sem autoallow, esquema de regras emprestado ou redução de authority. |

As documentações acima são exemplos de interação, não provas de que um produto é
melhor em geral nem um mandato para reproduzir seu comportamento. A decisão local
deve preservar state/authority e os contratos bounded do Workbench.

## Defeitos locais auditados e critérios

Os três itens prioritários para estas duas rodadas são os três primeiros abaixo.
Para cada um, os checks propostos exercitam comportamento determinístico; não
exigem endpoint ativo, inferência real nem lowering das permissões.

| Prioridade | Achado de código local | Correção planejada | Critério de aceite |
| --- | --- | --- | --- |
| P1 — rascunho | A auditoria inicial viu a seleção da paleta substituir texto não enviado. O worktree agora contém drafts temporários por workspace e ação **Recuperar rascunho**. | Manter esse limite temporário e a restauração manual. Sem persistência em disco nem envio automático. | Gate integrado dos cinco harnesses passou após as correções das duas rodadas. |
| P2 — leitura | A auditoria inicial viu mensagens/deltas forçando o scroll ao fim. O worktree agora captura âncora/posição e oferece **Ir para o fim**. | Seguir stream quando a pessoa já acompanha o fim; manter âncora de leitura acima do fim. | Gate integrado dos cinco harnesses passou após as correções das duas rodadas. |
| P1 — resultado | Auditoria inicial encontrou JSON cru como resumo e erro que ignorava `settlement`. O worktree atual adiciona resumo humano, detalhes JSON sob demanda e resultado parcial em erro. | Preservar os dados e deixar explícito que resultado parcial/falha não é sucesso; não repetir automaticamente. | Gate integrado dos cinco harnesses passou após as correções das duas rodadas. |

Esse inventário registra implementação local e evidência na rodada de UX atual.
A seção de resultados da
[estratégia de simplificação](estrategia-simplificacao-20261101.md) registra o
que de fato foi implementado e validado.
