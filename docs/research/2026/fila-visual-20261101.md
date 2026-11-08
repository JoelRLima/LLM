# Fila visual de pedidos no Workbench

## Uso básico

Mensagens enviadas enquanto outra tarefa está em execução ficam na fila existente da sessão. Agora o painel **Tarefas → Fila de pedidos** mostra cada mensagem, com **Ver pedido completo e ID**, **Enviar** e **Remover**. O usuário pode conferir ou remover um pedido durante a execução atual. **Enviar** fica disponível quando a sessão está livre e inicia somente o pedido escolhido.

O campo de mensagem permanece intacto ao enviar ou remover itens da fila. Nenhum item é enviado automaticamente ao concluir uma tarefa. IDs e comandos continuam disponíveis para uso técnico, mas não precisam ser digitados nesse fluxo.

## Implementação e limites

- `InteractiveSession.snapshot()` acrescenta `pending_items`, derivados do mesmo `PendingStore` que fornece `pending_count`. Cada item expõe somente ID e texto visível; o envelope interno e suas regras não são transferidos à interface. Consultar não consome nem executa pedidos.
- O frontend usa os comandos canônicos `/pending send ID` e `/pending discard ID` pela bridge existente. Não há outro escalonador, armazenamento de fila ou execução direta de ferramentas.
- A admissão continua pertencendo ao runtime. Uma rejeição antes da admissão mantém o item no store; um envio aceito consome somente aquele item. Repetir a ação de um item consumido não executa outra mensagem.
- O frontend confere projeto, ID, texto, estado e ação em andamento. Botões antigos, respostas atrasadas e cliques durante outra execução não ampliam a elegibilidade. O backend continua sendo a barreira definitiva de admissão.
- Falha de remoção mantém a fila visível e apresenta o motivo recebido. A lista é atualizada a partir do snapshot canônico após a ação. A confirmação de envio não é apresentada como conclusão da tarefa; o resultado continua seguindo os eventos normais.
- Snapshots ausentes, de outro workspace ou malformados não são interpretados como fila vazia. A interface oferece uma mensagem de indisponibilidade e o fallback `/pending` para sessões antigas. A projeção admite até 16 itens com textos de até 16.000 caracteres, de acordo com os limites da sessão atual.

## Verificação

- **583 testes aprovados, 14 ignorados**, cobrindo interfaces, comandos locais, navegação e integração do Workbench. [JUnit](../../../reports/ux-queue-tests-20261101.xml). Um aviso Tk de descarte fora da thread principal permanece registrado, como nas rodadas anteriores.
- Cinco novos testes verificam snapshot independente e sem consumo, envio exato uma única vez, rejeição e sessão ocupada preservando o item, e remoção pelo controle canônico sem afetar os demais pedidos.
- Os cinco harnesses JavaScript foram executados no Node REPL, pois Node não está no PATH. O harness de botões agora carrega o módulo de fila e verifica literalidade do texto, bloqueio durante execução, rejeição de remoção, preservação do rascunho, envio único, projeto antigo, snapshot inválido e resposta atrasada.
- [Registro dos harnesses JavaScript](../../../reports/ux-queue-node-20261101.json).
- Edge headless verificou os fluxos anteriores de perfil/recuperação e uma vista de fila: envio desabilitado durante trabalho, remoção somente do segundo pedido, envio do primeiro pelo comando canônico, preservação do rascunho e ausência de overflow horizontal. Usa bridge sintética e não chama modelo real.
- Ruff passou. O script de navegador passou no Mypy. A análise isolada de `InteractiveSession` com `--follow-imports=skip` apresenta os mesmos 16 diagnósticos `no-any-return` do pacote anterior; esse módulo não foi declarado limpo. [Comparação dos diagnósticos](../../../reports/ux-queue-type-baseline-20261101.json). Nenhuma configuração ou baseline de qualidade foi enfraquecida.
- Arquitetura CURRENT aprovada: 981 módulos e 3.464 arestas.

Não houve inferência real, medição de latência de modelo ou estudo com usuários. As verificações de interface são automatizadas; os testes do store usam o proprietário real e respostas de admissão simuladas. A inspeção visual do navegador complementa esses testes, sem equivaler a testar todo o host WebView nativo.

## Instalação conferida

Instalada `e1f222af37ba-20261102T011032Z-e675b8bc`, preservando `8bfafd3a2ca3-20261102T004713Z-01cf1901` como anterior. O status confirmou `update_available=false`, a integridade do pacote coincide com os metadados e o novo módulo JavaScript está presente na instalação. SHA-256 do wheel: `e8ab3e103d2c47f50447f43be4ad5835cfc9b3cc330ddf1eabf1bcc4445f5c48`.

O Python isolado (`-I`) do pacote instalado verificou snapshot e remoção pelo controle canônico, sem criar runtime de modelo. Edge headless repetiu as três vistas usando os assets instalados: recuperação escura, recuperação clara e fila com remoção seletiva/envio único. [Provas do navegador](../../../reports/ux-queue-installed-20261101/browser-proof.json) e [captura da fila instalada](../../../reports/ux-queue-installed-20261101/queue-desktop.png).
