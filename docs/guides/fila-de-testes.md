# Fila de testes de tarefas

O Workbench oferece o painel **Testes** na navegação principal. A CLI unificada
também oferece `llm-agent queue`; ambos usam os mesmos serviços da fila. No
checkout, `Workbench.cmd` reúne os pontos de entrada; **Baterias de tarefas**
abre este menu interativo. Também é possível executar ações diretas com
`Workbench.cmd queue COMANDO [OPÇÕES]` ou `python -m llm_agent.interfaces.test_queue`.

## Preparar tarefas

Cada subpasta representa uma tarefa; a ordem inicial é alfabética:

```text
minhas-tarefas/
  01-json/
    task.md
    checks.json
    seed/              (opcional, arquivos iniciais UTF-8)
  02-funcao/
    task.md
    seed/calculator.py
    verify.py          (opcional, verificação de comportamento)
```

No Workbench, **Criar uma tarefa simples sem editar arquivos** gera `task.md` e
um `checks.json` básico dentro da pasta escolhida. Você fornece o pedido e o
critério objetivo; pode incluir um arquivo inicial. Depois, clique em
**Preparar bateria** para importar as tarefas da pasta. Para verificações mais
ricas, crie `verify.py` manualmente. O assistente não transforma a resposta da
LLM em gabarito.

`task.md` contém o pedido. `checks.json` contém um objeto com a lista `checks`. Por exemplo:

```json
{"checks":[{"kind":"json_equals","path":"result.json","expected":{"message":"ok","count":3}}]}
```

Tipos: `exists` (arquivo regular), `absent`, `text_equals` (texto UTF-8 exato, inclusive quebras de linha), `json_equals` (JSON completo, sem depender de espaços/ordem de chaves) e `sha256`. Os três últimos exigem `expected`. Caminhos são relativos ao projeto de teste; escapes e links são rejeitados.

Se houver `verify.py`, ele também é importado como verificador. Recebe a pasta do projeto em `sys.argv[1]` e como diretório de trabalho. Saída zero significa aprovação; uma asserção ou saída diferente de zero reprova. Pode executar a bateria de testes do projeto usando executável e argumentos explícitos. Importações locais precisam ser explícitas, pois Python roda com `-I`. Os exemplos em [examples/test-queue](../../examples/test-queue) mostram comparação JSON e testes de uma função.

O pedido, arquivos iniciais e verificadores são copiados para um manifesto no momento da importação. Alterar as pastas originais depois não altera o experimento. Cada caso recebe um projeto e um home novos, retidos na pasta da fila. O verificador autoral é reconstituído fora do projeto a partir da versão importada antes de rodar. A LLM recebe o pedido e o projeto, não o conteúdo dos critérios externos.

## Usar pelo terminal

Execute a partir da raiz do repositório:

```powershell
llm-agent queue create ".\examples\test-queue" ".\reports\minha-fila"
llm-agent queue run ".\reports\minha-fila" --config "C:\caminho\config.json" --profile meu_perfil --live --yes
llm-agent queue status ".\reports\minha-fila"
llm-agent queue pause ".\reports\minha-fila"
llm-agent queue stop ".\reports\minha-fila"
llm-agent queue next ".\reports\minha-fila" 02-function
```

`llm-agent queue` sem argumentos abre o menu. `llm-agent queue --help` mostra
as ações disponíveis. Uma execução usa um perfil fixo; para comparar perfis,
crie filas diferentes. A fila inteira é importada de uma vez; não há edição da
fila durante a execução. `next` prioriza uma pendente com o runner parado. `run`
começa imediatamente quando a fila está disponível; não interrompe outra tarefa
para furar a fila. `Workbench.cmd queue` usa o `.venv` do checkout; o comando
instalado `llm-agent queue` usa o pacote ativo.

`--live` autoriza chamadas à LLM configurada e pode ter custo. `--yes` é opcional e autoriza automaticamente ferramentas; sem ele, operações que precisam de aprovação ficam bloqueadas pelo runtime, sem abrir uma espera interativa. Não há aprovação automática implícita.

`pause` termina a tarefa atual, inclusive verificação, salva e pausa antes da próxima. Repita `run` para retomar. `stop` cancela o processo atual e a árvore de processos pelo executor existente. É interrupção, não congelamento de uma chamada ativa.

Após queda ou interrupção, o caso ativo fica `interrupted` e não é repetido. Revise os artefatos e use `--skip-interrupted` para preservar esse caso e seguir as pendentes. Para repetir o experimento, importe uma nova fila. Não há garantia de desfazer efeitos externos. Apenas um runner pode possuir cada fila.

## Entender os resultados

`state.json` separa o resultado declarado pelo runtime da verificação externa. `verified` exige sucesso do runtime **e** aprovação de todos os critérios definidos. `unverified` significa sucesso declarado sem critérios externos. `failed` indica runtime sem sucesso ou critérios reprovados. `timeout`, `error` e `interrupted` mantêm essas causas explícitas. Uma fila `completed` apenas terminou de percorrer os casos; consulte os resultados individuais.

A verificação só confirma o que foi definido em `checks.json` e `verify.py`.
Ela não prova requisitos que não tenham critérios correspondentes; revise os
critérios antes de confiar na classificação `verified`.

São retidos resposta, recibo, referência ao relatório canônico, hashes de arquivos conferidos e saída limitada dos verificadores. `cases/<id>/` guarda projeto, home, logs e relatório. O estado é salvo antes do primeiro efeito e após cada caso. O comando retorna 0 somente se todos os casos forem verificados; 1 para falhas; 2 para fila incompleta, interrupção, erro de comando ou resultado sem verificação.

Padrões: 300 segundos por execução, até 30 segundos adicionais para todos os verificadores do caso, 3600 segundos acumulados de processamento da fila. A verificação também respeita o orçamento total restante. Ajuste com `--case-seconds` e `--total-seconds`. Os limites canônicos por tarefa são no máximo 20 chamadas, 30 etapas, 200 mil tokens, 30 ferramentas e 300 segundos, preservando valores mais restritos da configuração. O máximo total teórico de chamadas é 20 vezes a quantidade de casos; não é orçamento monetário. Tempos de limpeza de processos podem acrescentar pequena latência. O progresso contabiliza o processamento concluído; uma queda abrupta não permite medir com precisão o tempo perdido no caso ativo.

Limites de importação: 1..1000 casos; até 200 arquivos UTF-8 e 100 critérios por caso; arquivo individual até 1 MiB; manifesto total até 32 MiB. Arquivos binários e agendamento entre várias filas ainda não são atendidos.

A retomada recusa mudança de código/configuração/permissão automática/limite por caso, para não misturar experimentos. O hash identifica o código e a configuração usados; não congela qual modelo o servidor entrega sob um alias. A verificação só prova os critérios escritos: um teste fraco pode aprovar uma implementação incompleta.

Os projetos e homes separados evitam mistura de estado, mas **não são sandbox**. Ferramentas e verificadores têm os privilégios do processo local. Use somente tarefas e verificadores confiáveis; efeitos de rede ou fora do projeto continuam possíveis. Os relatórios, homes e configurações retidos podem conter dados sensíveis locais.

## Modularidade

`application/test_queue_manifest.py` interpreta os casos; `test_queue.py` mantém a fila e recebe um executor por contrato; `test_queue_runtime.py` adapta para os casos de uso canônicos de tarefas e para o executor de processos existente; `test_queue_verification.py` confere os artefatos sem consultar a LLM. `interfaces/test_queue.py` fornece menu e CLI. Não há dependência da fila sobre o Workbench nem novo planner/runtime.

O verificador externo não envia automaticamente outra solicitação à LLM nem inicia reparos. Ele registra a divergência para revisão; o agent mantém a verificação interna do runtime existente. Essa separação evita que a bateria aprove a própria declaração do modelo. Para dar feedback em uma nova tarefa, use o relatório e os artefatos retidos.

Erros de infraestrutura pausam as próximas tarefas; reprovações nos critérios e timeouts de casos ficam registrados e a bateria pode prosseguir. Resultados extensos ficam em `cases/<id>/result.json`, com referência no progresso compacto.

Validação desta entrega: 29 testes específicos da fila/integração passaram, incluindo subprocesso real com respostas de modelo simuladas; 695 testes de regressão passaram e 14 foram ignorados. Ruff passou e mypy passou nos cinco módulos novos. A correção de concorrência nas consultas usa o mesmo lock para publicar o resultado e liberar o worker. Nenhuma inferência real foi executada nesta validação.

## Conferir e entender a fila

No menu, as opções 7, 8 e 9 permitem conferir a preparação, gerar um HTML e explicar uma tarefa. A pasta da fila e o arquivo de configuração são lembrados durante a mesma abertura do menu.

```powershell
llm-agent queue check ".\reports\minha-fila"
llm-agent queue check ".\reports\minha-fila" --config "C:\caminho\config.json" --profile meu_perfil --yes
llm-agent queue details ".\reports\minha-fila" 01-json
llm-agent queue report ".\reports\minha-fila"
llm-agent queue status ".\reports\minha-fila" --json
```

`check` é uma conferência local: mostra critérios por tarefa, tarefas sem critérios, pastas não importadas e progresso. Com `--config`, resolve o perfil, mostra os limites e confere compatibilidade com a identidade já salva. Não cria gateway, não testa a conexão e não executa verificadores. `create`, `check`, `status`, `details`, `report`, `pause`, `stop` e `next` não fazem inferência. Use os mesmos valores de `--yes` e `--case-seconds` do experimento para conferir a retomada.

A execução mostra início e resultado de cada tarefa após salvar o progresso. Isso é progresso por caso; não é estimativa de tempo restante nem transmissão dos tokens da LLM. Uma falha na apresentação dessas mensagens não altera o resultado salvo.

`details` explica o sucesso declarado, a verificação externa e os critérios reprovados. `report` gera `report.html` na pasta da fila; permite `--output CAMINHO.html`. Dentro da fila, apenas `report.html` e a pasta `reports/` são destinos de exportação, protegendo os projetos e evidências. A exportação substitui o relatório derivado nesse destino.

O HTML funciona sem scripts nem recursos externos, acompanha o tema do sistema e coloca falhas primeiro. Os dados técnicos permanecem em seções expansíveis. A ordem de execução da fila não muda. É um retrato do progresso salvo no momento da geração, sem atualização automática. Pode conter respostas e caminhos locais: revise antes de compartilhar.

A saída padrão do terminal passou a usar rótulos e explicações em português. Para automações, `--json` emite um único documento JSON no stdout, sem as mensagens de progresso. Os códigos de saída mantêm seus significados.

A validação dos caminhos agora independe da pasta atual do processo. Caminhos reservados ou ambíguos no Windows e fixtures que se sobrepõem por diferença de maiúsculas/minúsculas são rejeitados antes da execução.

A composição da fila agora é centralizada em `application/test_queue_service.py`. Os adaptadores devem chamar `TestQueueService`, enviar `QueueExecutionRequest` e apresentar `QueueRunResult`. Configuração, autorização de chamadas reais e avaliação final pertencem ao serviço; códigos de saída, rótulos, menus e HTML pertencem às interfaces. Veja [a verificação do núcleo compartilhado](../history/2026/milestones/nucleo-compartilhado-20261102.md).

## Testes dentro do Workbench

Abra o Workbench e escolha **Testes** na navegação principal.

1. Em **Preparar uma nova bateria**, escolha a pasta das tarefas. Opcionalmente, abra **Criar uma tarefa simples sem editar arquivos** e defina o pedido, o arquivo de saída e o critério verificável; a pasta é criada ali. Clique em **Preparar bateria** para importar uma cópia das tarefas. Você não precisa escolher uma pasta de saída.
2. Escolha o perfil de modelo. **Limites e permissões** reúne os tempos por tarefa/fila e a autorização automática de ferramentas.
3. Clique em **Conferir preparação**. Essa etapa não executa verificadores nem chama a LLM.
4. Marque a autorização de chamadas à LLM e clique em **Iniciar / retomar**.
5. Acompanhe o progresso por tarefa, filtre resultados e use **Ver evidências** para comparar o sucesso declarado com a verificação externa.

A configuração e o código conferidos ficam vinculados ao início da execução. Alterações nesse intervalo impedem a chamada ao executor até que você confira novamente. Alterar perfil, limites ou permissões na tela também invalida a conferência anterior.

O modo da conversa não é aplicado à bateria. Cada caso usa o runtime compartilhado em seu próprio projeto e home. Sem autorização automática de ferramentas, operações que exigem aprovação ficam bloqueadas, sem espera interativa. Autorizar chamadas à LLM não autoriza automaticamente essas ferramentas. Não há sandbox de sistema operacional.

**Pausar após a atual** termina o caso e sua verificação antes de pausar. **Parar agora** solicita cancelamento e pode deixar o caso como interrompido; as pendentes são preservadas. A revisão das interrompidas continua explícita antes de seguir com as demais. A pausa não congela uma chamada ativa da LLM.

Fechar o painel mantém a execução. Mantenha o Workbench aberto; encerrar o app cancela a execução que ele possui, e o progresso persistido permite identificar a interrupção depois. A conclusão da fila não significa que todos os testes passaram.

As oito filas recentes ficam disponíveis no home da aplicação. Para abrir uma fila criada pela CLI, use **Abrir fila existente…** e selecione a pasta com manifest.json e state.json. Também existe a alternativa **Abrir uma fila pelo caminho**. Quando outro processo possui a fila, o painel permite acompanhamento e pedidos de pausa/parada, sem iniciar uma segunda execução dela.

O painel recupera perfil, limites e preferência de ferramentas de uma execução anterior; a autorização para chamar a LLM precisa ser marcada novamente. Credenciais não são enviadas na lista de perfis. Filas de versões diferentes podem recusar retomada para preservar a identidade do experimento.

O filtro destaca resultados que precisam de atenção após concluir. As evidências mostram o resultado declarado, os critérios externos e os dados técnicos expansíveis. A atualização preserva a leitura e o rascunho do chat. O HTML gerado continua disponível no caminho mostrado em **Identidade e pasta da fila**.

A interface usa a mesma TestQueueService e o mesmo controlador interativo já utilizados pelo núcleo. Não existe planner, executor de ferramentas ou política de avaliação implementado em JavaScript.

## Continuar após fechar o app

Marque **Continuar em processo independente** antes de iniciar a bateria.
Na CLI, acrescente `--detach` ao comando de execução autorizado:

```powershell
llm-agent queue run "C:\filas\regressao" --config "C:\config\agent.json" --live --detach
llm-agent queue status "C:\filas\regressao"
llm-agent queue pause "C:\filas\regressao"
llm-agent queue stop "C:\filas\regressao"
```

O retorno `starting` confirma o lançamento solicitado, não a conclusão nem o
início da primeira tarefa. Confira status e o log informado. O worker usa o
mesmo lock, identidade do runtime, limites e recuperação da fila. Uma tentativa
concorrente não executa a mesma fila. `--detach` não concede aprovação
automática de ferramentas. Fechar o app não cancela o worker independente.

Após queda, tarefas interrompidas requerem revisão; `--skip-interrupted`
preserva suas evidências e permite seguir. Não há repetição automática de
efeitos ou serviço que reinicie após reiniciar o computador.

## Criar caso de uma evidência revisada

```powershell
llm-agent queue draft "C:\tarefas\regressao-01" --objective "Criar result.txt com conteúdo aprovado" --checks "C:\revisao\checks.json" --evidence "C:\revisao\trace.jsonl" --json
```

O comando mostra preview sem escrever ou chamar LLM. O objetivo e os critérios
são fornecidos por quem revisa; a resposta do modelo não vira gabarito. Após
revisar, repita com `--write-reviewed`, adicione somente fixtures sanitizadas em
`seed/` e importe com `queue create`. A origem é registrada por SHA-256, sem
copiar conteúdo da evidência. Diretórios existentes não são sobrescritos e pelo
menos um critério externo é obrigatório.
