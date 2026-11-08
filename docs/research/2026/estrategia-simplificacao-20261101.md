# Estratégia incremental para simplificar o uso

## Diagnóstico

O repositório oferece um produto desktop e uma CLI, mas o README começa por
capacidades, limites técnicos e instalação Python. A jornada diária do usuário
Windows — abrir, escolher projeto/perfil, analisar ou editar e atualizar — aparece
misturada a instruções de desenvolvimento e a detalhes de ondas históricas. O
índice técnico também é útil para manutenção, porém não orienta primeiro o usuário
comum.

A simplificação deve reduzir decisões e explicações antes do primeiro uso,
preservando os owners atuais de runtime, workspace, permissões, validação,
aprovação, atualização e execução. O princípio é revelar a próxima ação conforme
a necessidade, com referências para os recursos avançados. Isso segue a orientação
de começar com o sistema mais simples que atende ao caso de uso e de melhorar
agentes por ciclos incrementais com progresso verificável. Consulte
[Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)
(use apenas os princípios gerais; o texto é de 2024 e suas referências a tooling
podem estar desatualizadas), [Effective Harnesses for Long-Running Agents](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents),
o [guia de avaliações de agentes da OpenAI](https://developers.openai.com/api/docs/guides/agent-evals)
e o [exemplo de ciclo de melhoria para agentes](https://developers.openai.com/cookbook/examples/agents_sdk/agent_improvement_loop).

## Fases

1. **Entrada cotidiana:** colocar no início do README os passos Windows para
   abrir, selecionar projeto pelo cartão **Escolher projeto**, selecionar perfil,
   analisar em `READ_ONLY`, pedir edição por comando explícito e atualizar
   localmente. A paleta contextual de workspace prepara um rascunho; o usuário
   preenche o caminho e envia. Preservar a instalação Python e a CLI como fluxo
   separado de desenvolvimento.
2. **Guia básico curto:** explicar comandos e recuperação comuns em uma página;
   deixar arquiteturas, limites completos, extensions, inspeção e histórico nos
   documentos avançados, ligados a partir dos pontos relevantes.
3. **Progressive disclosure na interface:** apresentar primeiro chat, arquivos,
   tarefas, workspace, perfil e modo corrente; manter ferramentas e artefatos
   recolhidos sob **Mais recursos**. Manter comandos avançados na paleta/ajuda,
   sem esconder informação necessária para consentimento, validação ou diagnóstico.
4. **Verificação incremental:** para cada alteração, rever apenas o caminho
   afetado, validar links e fatos operacionais, executar regressões offline
   selecionadas para qualquer mudança de produto e fazer revisão integradora da
   jornada. Não exigir servidor ou inferência real para comprovar a navegação e
   os limites offline.
5. **Estado e resultado legíveis:** deixar claro o que está configurado, o que
   respondeu ao catálogo de modelos, se o modelo configurado apareceu nesse
   catálogo e qual resultado/completude a tarefa produziu. Catálogo/metadados não
   comprovam geração; cancelamento ou resposta parcial não deve ser apresentado
   como tarefa concluída. Um estado succeeded pode ser uma operação local sem
   inferência. Só evidência da chamada real comprova geração de resposta pelo
   modelo. O diálogo de sessão pode mostrar contexto de workspace e perfil, modo
   e disponibilidade, sem expor segredo ou endpoint; os comandos da paleta seguem
   como rascunhos até envio explícito.

## Critérios mensuráveis de não regressão

- README e `docs/README.md` apontam para um guia básico existente; todos os links
  relativos adicionados resolvem para arquivos presentes.
- Em até 20 linhas do README, a pessoa encontra os nomes exatos dos launchers,
  um próximo passo para selecionar workspace/perfil, o modo inicial e a rota de
  atualização.
- O guia documenta os comandos aceitos (`/workspace switch`, `/model select`,
  `/modo editor`, `/modo read-only`) e não sugere edição sem escolha explícita.
- Nenhuma instrução de uso cotidiano promete permissões que o runtime não concede,
  contorna aprovação, faz download automático, instala pacotes ou invoca o modelo
  para atualizar/instalar.
- A UI e o guia distinguem configuração local, resposta do endpoint de catálogo,
  modelo listado e evidência de chamada real; nenhum teste de conectividade ou
  listagem usa rótulo de “pronto para inferência”. Estados de conclusão de tarefa
  não são usados como atalho para inferência, pois operações locais também podem
  concluir sem chamada a modelo.
- O diálogo de sessão mostra workspace/pasta, perfil/provedor/modelo, modo e
  disponibilidade; detalhes técnicos seguem allowlist sem endpoint nem chave. Ele
  não inicia sondas. Consultas do catálogo são ações explícitas e não invocam uma
  geração. Seleções na paleta preparam rascunhos e aguardam envio explícito.
- A UI mostra o estado da execução separado da completude do relatório. Um estado
  local succeeded não implica geração de modelo; cancelamento, erro ou documento
  incompleto não são apresentados como resultado completo. Usar os registros
  canônicos e preservar a diferença entre estado e completude.
- A instalação F1 preservada, o launcher gerenciado, a atualização via UI e os
  diagnósticos de log continuam documentados de acordo com seus owners atuais.
- Mudanças só documentais não adicionam chamadas de modelo nem exigem testes de
  produto. Se a UI ou runtime mudar, selecionar regressões offline por superfície
  afetada e registrar o resultado antes de encerrar a revisão.
- Antes de declarar a experiência simplificada, uma revisão integradora percorre
  os passos publicados no checkout real e confirma que o caminho técnico avançado
  permanece acessível sem ocupar a entrada cotidiana.

## Rodadas de melhoria de UX

A avaliação comparativa, os relatos públicos com seus limites e os achados locais
estão na [matriz de UX comparativa](avaliacao-comparativa-ux-20261101.md). Ela
separa relato de defeito reproduzido e usa a documentação de outros produtos como
padrão a avaliar, não como prova de superioridade nem especificação a copiar.

### Rodada 1 — rascunhos e leitura do transcript

**Escopo:** manter um rascunho anterior recuperável ao selecionar comandos,
apresentar restauração explícita e fazer a conversa seguir novas mensagens só
quando a pessoa estiver acompanhando o fim. Se ela estiver lendo acima, preservar
posição e oferecer um controle para voltar ao fim. Recuperação do rascunho deve
ser limitada e temporária, sem persistência em disco e sem envio automático.

**Aceite:** checks determinísticos cobrem alternância/restauração, limites,
workspace diferente, falha de identidade, envio só após ação explícita e o
comportamento de scroll com follow ligado/desligado. Controles ficam acessíveis a
teclado. Esta rodada não enfileira mensagem para execução automática.

**Estado em 01/11:** implementação de rascunho limitado em memória e leitura com
âncora/follow está no worktree. O gate integrado final aprovou os cinco
harnesses JavaScript após os ajustes das duas rodadas.

### Rodada 2 — progresso e resultado parcial

**Escopo:** substituir o despejo inicial de JSON por resumo legível de progresso,
mantendo o payload bruto sob demanda; representar estados terminais com precisão.
Se a execução reportar erro mas o settlement preservar uma observação parcial,
manter a observação visível como parcial, sem convertê-la em sucesso.

**Aceite:** harnesses cobrem concluído, cancelado, parcial, erro sem resultado e
erro com settlement/resultado parcial; uma resposta atrasada não sobrescreve a
execução corrente. A indicação de completude do relatório fica separada do estado
da execução. Não há retry automático, alteração das permissões, chamada de modelo
ao vivo ou clonagem do sistema de checkpoint de outro produto.

**Estado em 01/11:** o worktree contém resumo humano, JSON técnico expansível e
tratamento de settlement parcial em erro. Os cinco harnesses JavaScript passaram
no gate integrado; o ajuste preserva referências cujo `run_id` é nulo.

Cada rodada deve registrar evidência e falhas concretas antes de iniciar a
seguinte. A causa ou frequência de um relato de terceiro não deve ser atribuída a
este produto sem reprodução local. Ao mudar uma ação visível, atualizar o
[guia de uso básico](../../guides/uso-basico.md) depois que o texto da interface e os checks
estiverem confirmados.

## Custo e delegação

Trabalhar em lotes pequenos, preservar conteúdo técnico vigente e ajustar apenas
os pontos de entrada reduz retrabalho e consumo de contexto. Não há base para
prometer uma porcentagem fixa de economia de tokens ou quota: custo depende do
modelo, contexto e ferramentas escolhidos. Delegar revisão somente quando houver
trabalho realmente independente, com escopo e arquivos delimitados; para uma
mudança curta de documentação, uma execução sequencial com revisão integradora
evita contexto duplicado e conflitos. Nenhum plano deve assumir quotas gratuitas,
capacidade futura ou disponibilidade de modelo específico.

## Resultados da rodada inicial de simplificação

Três frentes de trabalho Luna foram implementadas e passaram por revisão
integradora do coordenador. A revisão integrada executou o conjunto afetado:
**154 passaram, 5 foram ignorados, em 38,30 s**. Os cinco skips correspondem a
harnesses JavaScript sem Node disponível no `PATH`; os cinco harnesses foram
executados separadamente pelo Node REPL e aprovados. A checagem Ruff do teste do
launcher e a revisão do diff também passaram. Esses resultados cobrem o escopo
validado nesta rodada; não afirmam cobertura total nem ausência universal de
regressões.

A captura visual nativa não foi concluída: a aprovação do app Computer Use expirou
por timeout, sem recusa do usuário e sem screenshot. Não houve inferência real de
modelo nem medição de quota. Após a correção do caminho do log no README, a
atualização offline gerou o candidato
`fe12a3506840-20261101T135804Z-1ee425f5` (versão `0.2.0rc1`) com
`update_available=false`; o candidato anterior
`e3e8783566c3-20261101T135632Z-dc4225bb` foi preservado.

## Resultados da rodada de clareza de sessão

A rodada integrada de Python reportou **154 passed, 5 skipped, 1 warning em
44,98 s**. O único warning foi `PytestUnraisableExceptionWarning` relacionado a
`tkinter.Variable.__del__` com `RuntimeError: main thread is not in main loop`; o
teste não falhou e a causa não foi determinada. A verificação dos testes JavaScript
passou nos cinco harnesses após as correções de DOM obsoleto, recuperação da sonda
e descarte de respostas atrasadas. A checagem do diff e os links nos dois guias
também passaram.

Esta rodada deixa a sessão em diálogo separado, com perfil e contexto, status
compreensível e detalhes técnicos em allowlist, sem endpoint ou segredo. **Ctrl+K**
e **F2** abrem a paleta; o diálogo controla foco e os itens selecionados continuam
como rascunhos até envio explícito. A sonda recupera a disponibilidade após falha,
sem converter resposta de catálogo em prova de geração.

Não houve captura visual nativa nem inferência real de modelo nesta rodada; os
resultados acima cobrem os harnesses e checks citados, sem afirmar cobertura total.
A instalação offline final gerou o candidato
`20bd0e07fb1b-20261101T144232Z-2d6af9d1` (versão `0.2.0rc1`) com
`update_available=false`.

## Resultados das duas rodadas de UX comparativa

A suíte integrada de Python terminou com **154 passed, 5 skipped, 1 warning em
44,70 s**. O warning foi `PytestUnraisableExceptionWarning` em
`tkinter.Variable.__del__`, com `RuntimeError: main thread is not in main loop`;
não houve falha e a causa não foi determinada. Os cinco harnesses JavaScript
passaram contra os fontes finais. A cobertura final inclui rejeição/carregamento
de Tarefas e respostas antigas após uma atualização mais recente do mesmo painel;
referências com `run_id` nulo são preservadas. `git diff --check` e os links
relativos dos três documentos também passaram.

As primeiras capturas Edge headless vieram de uma cópia isolada do frontend com
bridge falsa: home, diálogo Sessão e painel Tarefas em 1280×800, mais Sessão em
900×650. A revisão do coordenador encontrou o resumo de Tarefas sem estilo porque
um seletor CSS usava classe enquanto o HTML fornecia ID. A regra foi corrigida
para o seletor por ID e o painel foi renderizado novamente. Também foi gerada uma
visão compacta de leitura com histórico e rascunho sintéticos. Na revisão, foi
encontrada uma segunda falha visual: em 900×650, **Ir para o fim** sobrepunha o
composer. O CSS foi alterado para colocar o botão no fluxo normal da página, e as
três visões de leitura foram renderizadas novamente em 900×650, 900×800 e
1280×800. A inspeção e revisão do coordenador confirmaram o botão sem sobreposição
nos cenários revisados e **Recuperar rascunho** visível em 900×650; o painel
Tarefas também foi renderizado novamente após a correção do seletor por ID.
Essas capturas representam apenas navegador Edge headless com fixture e bridge
falsa, não janela nativa, bridge real ou inferência. Não houve inferência real de
modelo. A atualização gerenciada ocorreu após as verificações.
A atualização gerenciada offline finalizou no candidato
`5bea126ab553-20261101T151334Z-4f267292`. O SHA-256 do wheel é
`cd780dfebf2850a71ad4f139d1e181c7d828a6f686eac3f8607878c26b2b7b9a`, com
fingerprint de origem `5bea126ab553d513b3dfe339b0869d93fa47409aa543db100037e43bd93a4e18`;
o status final foi `update_available=false`. O candidato anterior
`20bd0e07fb1b-20261101T144232Z-2d6af9d1` foi preservado.
