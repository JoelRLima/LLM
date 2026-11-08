# Auditoria e correções de UX — 21/10/2026

Branch: `fix/cli-ux-refinement`. Base conferida: `main`, HEAD
`9b5a2ddc2645430142e3c1ecaf6ef95c41e40406`, tree
`7f7fa25727419bf66f3b1503bd182fd7d2a7220b`, sem alterações locais iniciais.
Esta rodada não constitui aceitação formal da W23.

## Diagnóstico e prioridades

| Prioridade | Causa observada | Correção |
| --- | --- | --- |
| Funcional | O seletor tentava interpretar `/ls` como escolha; a descrição não era exibida. | Na paleta, um comando digitado volta ao compositor como rascunho. Número/nome, instruções, descrições e cancelamento explícito. |
| Funcional | Ctrl+C/F2 herdavam os atalhos do chat durante prompts de uma linha. | Atalhos contextuais; Ctrl+C cancela o prompt, F2 não abre outra paleta dentro dele. Ctrl+J envia nesse contexto e continua inserindo linha no chat. |
| Funcional | O adaptador convertia cancelamento em string vazia, podendo aceitar o modelo padrão. | Preservação de `None`; cancelar não aceita o padrão. |
| Funcional | `/doctor --online` no chat ignorava a flag. | Encaminhamento explícito ao diagnóstico canônico; `/doctor` continua local. |
| Clareza | Ajuda completa, aliases e operações administrativas apareciam juntos. | `/help` orienta a conversa e mostra nove comandos principais; `/help all` preserva todos os comandos e aliases. |
| Clareza | A descoberta inicial misturava comandos do chat, administração e Engineering. | Menu inicial com os mesmos nove comandos da ajuda; busca explícita mantém acesso ao catálogo completo. O filtro ocorre antes do limite de resultados. |
| Clareza | IDs internos ocupavam a apresentação do seletor. | IDs continuam válidos para compatibilidade, mas a lista mostra nomes/comandos e descrições. |
| Clareza | `default` parecia identificar um modelo efetivo. | Modelo e provedor configurados na abertura, `/model` e `/status`; alias explicitamente não confirmado. A interface não inventa um resultado de conectividade. |
| Clareza | Capacidade `READ_WRITE` parecia contradizer o modo `READ ONLY`. | Diagnóstico distingue capacidade do filesystem de permissão do Agent; abertura aponta para `/modo`. JSON e permissões permanecem iguais. |
| Clareza | `/where` ocioso mostrava campos `unknown`. | Orientação para iniciar uma tarefa; durante execução, ausência de eventos é informada sem fabricar atividade. |
| Clareza | Chooser sugeria pasta de sistema e faixa numérica fixa. | Orientação contextual, aviso em pastas de sistema, escolha explícita nesses casos, faixa dinâmica, `q` e EOF canceláveis. Paths são impressos literalmente. |
| Clareza | Textos da CLI tinham mojibake; catálogo em inglês. | Correção dos textos de modo, histórico, diagnóstico e primeira execução; títulos e descrições traduzidos na fonte canônica. |

Não foi criado outro registry, parser, controlador de tarefas ou mecanismo de
aprovação. `PRIMARY_ACTION_IDS` é apenas uma projeção de apresentação dos IDs
existentes. O filtro opcional da projeção Discovery reutiliza seu índice,
ranking e disponibilidade. A extração de `shell_bindings.py` mantém o dono
único de stdin e evita aumentar a complexidade do shell.

## Segurança e compatibilidade

- Selecionar ou digitar `/attention approve` na paleta apenas prepara texto;
  a execução exige um novo envio pelo chat e a admissão canônica.
- Aprovações continuam vinculadas à solicitação e geração corretas. Texto
  comum não aprova operações. Modo, grants e authority não foram ampliados.
- `/cancel` comunica uma solicitação, sem declarar que o worker já encerrou.
- Comandos preferidos, aliases, JSON do diagnóstico e recursos avançados
  continuam disponíveis. O modo inicial do chat continua `READ ONLY`.
- Não houve publicação, merge, alteração de registros formais de aceitação
  da W23 ou modificação da instalação estável. Os testes usaram temporários.

## Evidências

- Regressão direcionada final: **396 aprovados, 9 ignorados**. Inclui todos
  os testes de interfaces, catálogo de ações, diagnóstico standalone e
  integrações de compatibilidade de ações/queries da W19, além dos testes
  de modos operacionais. A suíte global revelou duas regressões de texto:
  ausência de `/modo` e `/code help` na ajuda e remoção do termo `grants` na
  explicação dos modos. Ambas foram corrigidas e revalidadas nesse conjunto.
- Foram acrescentados 24 casos parametrizados de UX: rascunho sem execução,
  retorno ao chat, restauração de rascunho, opções ambíguas/indisponíveis,
  entrada numérica excessiva, menu vazio, cancelamento com padrão, filtro
  anterior ao limite, mojibake da CLI, terminal com 32/80/120 colunas,
  estado ocioso, modelo `default`, escolha de workspace e diagnóstico online
  simulado. Teclado exercitado com Prompt Toolkit e pipe real de entrada.
- Ruff e mypy para Linux/Windows aprovados; `git diff --check` aprovado.
- `check_quality.py` aprovado em worktree isolado com as alterações. Limites
  e baseline do projeto não foram relaxados.
- Wheel final em ambiente limpo: **aprovado**, incluindo a correção do filtro
  do menu; saída `Installed package clean acceptance passed.`.
- Suíte completa no worktree isolado: **6007 aprovados, 72 falhas, 59
  ignorados e 5 erros**, em 29m12s. Esse resultado precede as duas correções
  de texto descritas acima; elas passaram na regressão final. A suíte global
  não é declarada verde.

Classificação das ocorrências globais, comparando os mesmos módulos com a
`main` intacta:

| Ocorrência | Resultado da investigação |
| --- | --- |
| 62 falhas também presentes na base | 35 em manifesto/materialização, 11 em cenários adversariais de distribuição, 1 no build driver, 14 no workflow de finalização e 1 de portabilidade do instalador. Permanecem fora da correção de UX. |
| 7 falhas e 5 erros de prova histórica | Verificações de successor/inventário exigem os bytes de produção anteriores. A branch altera esses bytes; os registros e hashes históricos foram preservados. Sua atualização exige avaliação de governança autorizada, não alteração silenciosa nesta entrega. |
| 1 falha de observação nativa | Durante a suíte, a operação sintética retornou `UNKNOWN`. O mesmo caso passou na base e passou novamente, isolado, no candidato: `1 passed in 13.27s`. Não há comprovação de defeito de UX; manter acompanhamento da intermitência. |
| 2 regressões de ajuda/modos | Corrigidas após a suíte e aprovadas na regressão final de 396 testes. |

A comparação de nomes dos casos está em `.tmp/ux-failure-comparison.json`;
nenhuma falha encontrada nas execuções de comparação da base ficou fora
do conjunto global observado. O bloqueio de ACL foi reproduzido também
com o temporário padrão do usuário. Nenhuma ACL foi relaxada.

Logs locais desta rodada: `.tmp/ux-final-regression-with-modes.log`,
`.tmp/ux-quality-final-copy.log`, `.tmp/ux-ruff.log`,
`.tmp/ux-mypy-linux-final.log`, `.tmp/ux-mypy-win-final.log`,
`.tmp/ux-final-copy-installed.log` e `.tmp/ux-full-isolated-final.log`.
Comparações com a base: `.tmp/ux-main-candidate.log`,
`.tmp/ux-main-candidate-regressions.log`,
`.tmp/ux-main-broad-comparison.log`. Rechecagem nativa:
`.tmp/ux-native-overhead-recheck.log`.
Os worktrees de validação ficam em `.tmp/ux-main-baseline` e
`.tmp/ux-validation`; não são instalações do produto.

O sandbox causou `PermissionError` na criação/leitura dos temporários do
pytest. As execuções válidas usaram acesso local ampliado e diretórios
isolados. A varredura de qualidade foi transferida para um worktree com os
mesmos arquivos candidatos para evitar artefatos históricos do checkout.

O primeiro gate instalado falhou no cenário D1 de receipt/descriptor de uma
extensão. O gate da `main` intacta e uma repetição do candidato passaram.
Essa falha intermitente deve ser acompanhada; não foi tratada como defeito
resolvido por esta rodada de UX.

## Limites e próximos testes

As superfícies principais da CLI estão prontas para testes práticos
controlados de UX; o gate global ainda não está aprovado. Não foi
carregado Qwen nem consultado seu endpoint; as verificações de interface,
execução e aprovação foram determinísticas e a rede do diagnóstico foi
simulada em testes.

Prontidão: **sim para iniciar uma campanha funcional controlada de UX com
modelo real; não para aprovação global, publicação ou aceitação da W23**.
Antes de uma aprovação global, será necessário resolver as limitações da
base/ambiente e avaliar os inventários vinculados ao novo candidato pelo
processo autorizado. Os testes práticos de UX podem começar sem mudar essas
fronteiras.

Uma campanha com modelo real ainda deve observar streaming, latência,
interrupção durante inferência, desconexão, revisão de aprovação, tarefa em
andamento e troca de perfil/workspace após settlement. Incluir PowerShell
interativo em janela estreita e o seletor nativo de pastas: renderização em
memória e entrada por pipe não substituem essa verificação visual.

Persistem mensagens técnicas em inglês em superfícies avançadas e existem
strings corrompidas fora da CLI, por exemplo em `agent/code/outcome_proposal.py`,
`agent/skills/file_reader_evidence.py` e heurísticas de `agent/interaction/guards.py`.
Estas últimas exigem revisão própria de compatibilidade e admissão; uma
substituição global de encoding poderia alterar comportamento. Esta entrega
não afirma que todo o backend está livre de mojibake.

O menu é textual, por número ou nome; não foi implementada navegação visual
por setas. A identidade efetiva por trás de `default` depende do servidor e
continua explicitamente não confirmada. Recomenda-se configurar um nome
explícito de modelo antes da campanha, conforme o perfil realmente usado.
