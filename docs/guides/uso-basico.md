# Uso básico do Workbench

> Uso local e botão Ativar LLM: [guia atualizado](uso-local-e-llm.md). O Workbench inicia sem gerar chamadas ao modelo.

Este guia cobre a rotina comum no Windows. Os detalhes técnicos e administrativos
continuam no [índice de documentação](README.md).

Também é possível abrir uma sessão local no terminal com `llm-agent local`.
Ela começa sem usar a LLM, aceita as mesmas ações de projeto da sessão do
Workbench e usa a pasta atual como workspace. Informe `--workspace DIR` para
escolher outra pasta. Use `/llm status` para consultar o estado sem rede,
`/llm test` para consultar explicitamente o catálogo do servidor sem gerar
texto, ou `/llm on` para liberar o modelo; ativar não envia o texto que já
estava no prompt. `llm-agent chat` também inicia a sessão compartilhada com LLM
desativada. Para manter a sessão antiga durante a migração, use
`llm-agent chat --legacy-session`.

## Abrir e preparar a sessão

1. Execute `Workbench.cmd` na pasta do checkout e escolha **Abrir Workbench**.
   Para abrir diretamente sem o menu, use `Workbench.cmd open`. O launcher abre
   a instalação gerenciada ativa; se ainda não houver uma, tenta abrir a
   instalação F1 preservada e explica como migrar.
2. Confira o projeto e a pasta na **Visão geral**. Use **Trocar projeto** para
   selecionar outra pasta. A sessão precisa estar ociosa; a troca inicia uma
   sessão nova em `READ_ONLY`.
3. Comece pelas ferramentas locais na visão geral: consulte **Arquivos, Git e
   análise**, prepare uma **Bateria de testes** ou confira **Execuções e
   resultados**. Essas funções não precisam de perfil nem chamada à LLM.
4. Quando quiser conversar ou executar uma tarefa com IA, abra **Assistente** e
   use **Ativar LLM** no topo. Ativar libera o uso, mas não envia um pedido.
   Use **Escolher perfil** na Visão geral ou abra **Sessão** e escolha
   **Perfil e modelo** para selecionar um perfil configurado. Se a lista estiver
   vazia, use **Configurar perfil** para salvar um endpoint compatível, ou edite
   a configuração local se precisar definir credenciais de um provedor remoto. A
   conversa começa em `READ_ONLY`; alterações continuam sujeitas às validações
   e aprovações apresentadas pelo aplicativo.

## Entender o estado da sessão

Abra **Sessão** para conferir workspace e pasta, perfil, provedor, modelo, modo e
estado informado pelo runtime. Os detalhes técnicos mostram apenas campos
permitidos e não exibem endpoint ou chave.

Os estados respondem perguntas diferentes. **Modelo não configurado** significa
que não há perfil de modelo ativo. **Catálogo respondeu** significa que o
endpoint respondeu à consulta de metadados `/models`; isso não envia uma tarefa.
**Modelo listado no catálogo; geração não verificada** indica que o ID configurado
apareceu nessa lista, mas não confirma que o modelo aceita ou conclui uma geração.
**Inferência não verificada** ou **Estado desconhecido** também não comprovam
geração. **Consultar catálogo**, na barra superior, consulta `/models`; não executa
inferência.

Para confirmar uma geração, precisa haver evidência da chamada real ao modelo.
Uma tarefa `succeeded` pode ter feito apenas uma consulta local e não prova, por
si só, que houve inferência. Confira o estado da execução em **Tarefas** e abra o
relatório para ver a indicação de completude do documento, quando houver; ela
descreve o relatório, não o sucesso geral da execução. Cancelamento, erro ou
relatório parcial não equivalem a trabalho concluído. Consulte
[observabilidade e inspector](../observability-inspector.md) para a evidência técnica
detalhada.

## Acompanhar uma tarefa e revisar o resultado

Em **Tarefas**, o resumo explica estados como “Execução em andamento”, “Execução
aguardando sua atenção” e “Finalizando a execução; aguardando o resultado”. Ele
mostra somente etapa/ferramenta, última atividade, atenção e contagens que o
runtime reportou; não estima percentual. Abra **Dados técnicos da execução** para
ver a projeção bruta.

Mensagens pendentes aparecem como fila e não são enviadas ao terminar a tarefa.
Para enviar uma delas, use `/pending send ID` explicitamente. Cancelamento
solicitado não significa que a execução já terminou; aguarde o estado final. Se
uma falha chegar com observação ou relatório parcial, a UI preserva esse resultado
como parcial, sem marcá-lo como sucesso nem repetir a tarefa. Quando houver uma
referência, **Abrir resultado parcial** abre o conteúdo só após clique. A indicação
de completude do documento continua separada do estado geral da execução.

## Recuperar rascunho e continuar lendo

Ao escolher um comando na paleta, o texto existente pode ser substituído pelo
comando preparado. Se isso ocorrer, use **Recuperar rascunho** para alternar entre
o rascunho atual e os que foram substituídos; a ação não envia nenhuma mensagem.
Essa recuperação fica apenas em memória, limitada a 4 workspaces, 4 rascunhos por
workspace e 16.000 caracteres por rascunho. Ela é limpa ao trocar de workspace ou
encerrar a sessão. Se faltar a identidade do workspace ou o rascunho ultrapassar
o limite, o comando não substitui o texto.

Ao rolar a conversa para cima, novas mensagens e trechos preservam o ponto de
leitura. **Ir para o fim** aparece quando há conteúdo mais recente; clique para
voltar à última mensagem. Isso não envia nada e devolve o foco ao transcript. Se
você já estiver no fim, novas mensagens continuam acompanhadas automaticamente.

## Solicitar uma edição

A sessão começa em `READ_ONLY`; nesse modo, peça análise e consulta. Quando quiser
alterar arquivos, envie `/modo editor` e descreva a mudança. O runtime ainda
aplica suas regras de authority, validação e aprovação. Revise o diff e cada
solicitação apresentada antes de decidir. Para voltar ao modo de consulta, use
`/modo read-only`.

## Atualizar o Workbench

Use **Atualizações** no aplicativo para verificar e instalar as mudanças locais
salvas no checkout; depois escolha **Reiniciar agora**. No menu `Workbench.cmd`,
escolha **Gerenciar instalação** e depois **Instalar / atualizar**. A atualização é
local e não usa o modelo. Se o menu informar que faltam Python ou dependências
locais, a operação para com diagnóstico; consulte
[instalação e atualizações locais](../workbench-atualizacoes-locais.md).

## Se algo não funcionar

- **A janela não abre:** verifique `.workbench-managed/logs` e o arquivo
  `maintenance.log` dentro do candidato em `.workbench-managed/versions/`. Se
  não houver instalação gerenciada ativa, abra `Workbench.cmd`, escolha
  **Gerenciar instalação** e depois **Instalar / atualizar**.
- **O perfil não aparece ou a conexão falha:** confirme o nome do profile, o
  endpoint e o modelo no arquivo de configuração indicado pelo setup existente.
  **Consultar catálogo** faz uma verificação explícita do catálogo de modelos;
  não executa uma tarefa.
- **A edição foi recusada ou pede aprovação:** confirme que a sessão está em
  `editor` e leia o motivo e o diff mostrados. Mudar o modo não ignora authority,
  validação ou aprovação.
- **A tarefa está ocupada:** aguarde a conclusão antes de trocar workspace ou
  perfil. Para interromper, use **Parar** e espere a tarefa encerrar.

Para comandos além da rotina comum, consulte o [guia técnico do Workbench](../agent-workbench.md).
Para entender a origem e os limites dessas prioridades de UX, veja a
[avaliação comparativa](../research/2026/avaliacao-comparativa-ux-20261101.md).
