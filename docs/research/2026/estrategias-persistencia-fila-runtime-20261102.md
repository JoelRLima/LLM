# Estratégias para persistência da fila e identidade do runtime — 02/11/2026

Estado: investigação com experimento local e correções de compatibilidade. A fila
continua usando JSON; a adoção de outro formato depende dos critérios abaixo.
Este documento registra uma recomendação, sem substituir os contratos CURRENT.

## Problemas confirmados no código

- `application/test_queue.py:save` publica `state.json` inteiro com serialização,
  sincronização e substituição atômica. Há uma gravação antes dos efeitos de cada
  caso e outra depois da verificação. Mesmo com evidências em arquivos separados,
  a tabela completa dos casos é regravada. Para N casos, isso produz trabalho de
  serialização/gravação que cresce como N². O tamanho dos resultados também afeta
  a constante; a quantidade real de I/O físico depende do sistema de arquivos.
- `application/test_queue_runtime.py:runtime_identity` percorre arquivos `.py` e
  `.json` do pacote e calcula hashes do conteúdo. `RuntimeExecutor` repete essa
  operação antes e depois de cada caso concluído normalmente. Também há chamadas
  na preparação e construção do executor. O custo de conteúdo é aproximadamente
  2 × N × bytes do pacote, além dessas chamadas iniciais.
- Essa identidade cobre os arquivos selecionados do pacote e a configuração.
  Não fixa automaticamente o interpretador, dependências nativas, código de
  plugins externos ou todos os dados do ambiente. As verificações nas fronteiras
  também não impedem uma alteração transitória restaurada entre duas medições.

## Persistência: comparação das alternativas

| Alternativa | Benefício | Custo e risco | Avaliação |
| --- | --- | --- | --- |
| JSON compacto e menor frequência de salvamento | Mudança pequena; formato atual preservado | A escrita continua proporcional à fila inteira. Reduzir a frequência pode perder a marcação durável anterior aos efeitos | Somente otimização complementar |
| Um JSON por caso e cabeçalho pequeno | Escrita proporcional ao caso alterado; inspeção manual fácil | Atualizar caso e totais exige protocolo de recuperação entre arquivos. Consultar toda a fila pode abrir N arquivos | Viável, mas amplia o protocolo próprio de persistência |
| JSONL com eventos e checkpoints | Acrescenta eventos sem regravar o passado; histórico natural | Exige tratar última linha incompleta, sequência, replay, compactação e concorrência. Um checkpoint ingênuo pode devolver o custo anterior | Melhor candidato de escrita no experimento; recuperação ainda precisa ser demonstrada |
| SQLite com atualização de linhas | Transação conjunta para caso e estado da fila; consultas e recuperação já fornecidas pelo banco | Requer adapter, schema e compatibilidade de formato; sincronização durável apresentou custo maior localmente | Considerar pelas transações/consultas; não adotar apenas para acelerar a fila |

SQLite funciona sem servidor separado e tem interface no módulo `sqlite3` do
Python. Sua disponibilidade deve ser conferida na instalação, pois esse módulo
pode faltar em distribuições personalizadas. [Disponibilidade do módulo](https://docs.python.org/3/library/sqlite3.html).
Usar APIs compatíveis com o mínimo
Python 3.10 do projeto, como transações explícitas, evita exigir parâmetros
introduzidos em versões posteriores. [Documentação do Python 3.10](https://docs.python.org/3.10/library/sqlite3.html).

**Não basta guardar o JSON inteiro em uma única linha SQLite.** Isso preservaria
a serialização e atualização do documento completo. O adapter precisa receber
operações explícitas: criar fila, consultar resumo, marcar caso como iniciado,
concluir caso, mudar estado da fila e priorizar um caso pendente.

Estrutura proposta: metadados da fila em uma linha, resumo/status/posição de cada
caso em linhas separadas e evidências completas nos arquivos canônicos por caso.
Uma transação curta confirma a marcação de início antes do primeiro efeito.
Outra confirma resultado e tempo acumulado depois da publicação das evidências.
O trabalho externo do agente nunca ocorre dentro de uma transação aberta.
Uma priorização pode atualizar várias posições; ela não pertence ao caminho
executado a cada caso. Consultar/exibir todos os casos continua proporcional a N.

### Journal e durabilidade

O Python local e o `.venv` consultados usam Python 3.12.10 e SQLite 3.49.1. A
documentação atual do SQLite relata um problema raro de concorrência no modo
WAL, corrigido em 3.51.3 e em backports específicos. A versão 3.49.1 está dentro
do intervalo descrito como potencialmente afetado. Isso fundamenta testar
primeiro `journal_mode=DELETE`, sem habilitar WAL automaticamente nesta instalação.
[Descrição oficial do problema e versões corrigidas](https://sqlite.org/wal.html#walreset).

Para o experimento com DELETE, usar `synchronous=EXTRA`: o SQLite documenta a
sincronização adicional do diretório após excluir o journal e recomenda EXTRA
quando se deseja durabilidade nesse modo. Não justificar ganho desabilitando
sincronização de commits. [Garantias e limites de synchronous](https://sqlite.org/pragma.html#pragma_synchronous).

WAL pode ser avaliado posteriormente com uma versão corrigida, leituras curtas,
política de checkpoint e backup pela API apropriada. Ele permite concorrência
entre leitura e escrita, mas depende de processos no mesmo computador e não é
uma solução geral para filas em sistema de arquivos de rede.
[Restrições oficiais de WAL](https://sqlite.org/wal.html).

### Modularidade e compatibilidade

1. Extrair um contrato de persistência usado pelo scheduler, sem imports das
   interfaces. O adapter JSON mantém filas atuais; os candidatos incrementais
   atendem ao mesmo contrato. App, CLI e worker continuam usando
   `TestQueueService`.
2. Preservar as projeções existentes: resumo leve para acompanhamento, evidência
   completa sob consulta e retorno legado limitado para chamadas diretas de
   `TestQueue.run`. O formato físico não deve aparecer como escolha no uso comum.
3. Introduzir versão de formato explícita e recusar versões desconhecidas. No
   primeiro estágio, testar o candidato somente em filas novas; filas JSON
   continuam pelo adapter atual. Isso permite validar sem converter dados.
4. Tratar migração de filas existentes como operação separada, sob o lock do
   runner, com backup, validação e recuperação de interrupções. Nunca converter
   fila ativa. Não manter JSON e banco como duas autoridades gravadas em paralelo.
5. Impedir que um executável antigo retome silenciosamente uma fila já migrada.
   Um JSON de exportação pertence a `reports/`, não deve simular o estado ativo
   para binários antigos. Um protocolo de migração deve testar falhas entre cada
   publicação/renomeação; trocar dois arquivos não é uma transação única.

## Runtime: comparação das alternativas

| Estratégia | Reduz leitura? | Limite da garantia | Recomendação |
| --- | --- | --- | --- |
| Cache por tamanho/data/inode | Sim | Conteúdo pode mudar preservando metadados; um nome novo pode escapar de uma lista antiga | Somente para projeções e acompanhamento |
| Watcher de arquivos e hashes incrementais | Sim, na operação comum | Notificações podem ser perdidas; exige reconstrução completa e suporte específico de SO | Sinal auxiliar, nunca única prova de integridade |
| Árvore de hashes | Reaproveita hashes quando as alterações são conhecidas | Ainda precisa descobrir alterações de modo confiável; o formato da árvore não resolve a confiança nos arquivos | Complemento a um produtor controlado |
| Artefato único com hash completo antes/depois | Reduz abertura/enumeração de arquivos; continua lendo os bytes do artefato | O worker precisa importar esse artefato; custo de importação pode anular o ganho | Prototipar com bytecode da versão exata do Python |
| Release identificada por conteúdo e protegida pelo SO | Sim, pode validar uma vez por artefato | O worker precisa carregar esse mesmo artefato; dependências/plugins também precisam de política definida | Melhor direção para instalações estáveis |
| Hash completo no checkout mutável | Não elimina o custo | Detecta divergência presente nas fronteiras; não oferece isolamento contra o mesmo usuário do SO | Manter para desenvolvimento e como fallback |

No Windows, `ReadDirectoryChangesW` pode perder o registro de mudanças e exigir
nova enumeração após `ERROR_NOTIFY_ENUM_DIR`. Portanto, um watcher não deve
autorizar sozinho o reaproveitamento de uma identidade.
[Documentação da Microsoft](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-readdirectorychangesw).

Existe uma etapa intermediária à proteção imutável: conferir todo o conteúdo de
um arquivo de runtime antes/depois de cada caso, mantendo essa detecção nas
fronteiras. Ela reduz operações de arquivos sem trocar hashes de conteúdo por
metadados. O protótipo ZIP com fontes e bytecode teve melhor custo combinado
estimado; o ZIP somente com fontes piorou a importação. O Python admite `.py` e
`.pyc` em ZIP, não carrega extensões nativas diretamente dele e alerta para o
custo de importação quando faltam `.pyc`.
[Limites oficiais de zipimport](https://docs.python.org/3.10/library/zipimport.html).

A viabilidade parcial foi conferida: importar o adapter headless do próprio ZIP
e ler os defaults com `importlib.resources` funcionou. A execução completa ainda
exige adaptar `runtime_identity`, bootstrap dos workers e caminhos físicos de
`extensions/stdio_launcher.py`; esses pontos usam `Path(__file__)`. O bytecode
precisa corresponder à versão/cache tag do interpretador e ao conteúdo de fonte
do snapshot. Fontes e bytecode entram no hash do artefato. O pacote executado e
o pacote identificado precisam ser o mesmo; não basta gerar um ZIP e continuar
executando o checkout. Os testes de plugins em subprocesso e paths devem fazer
parte da promoção desse candidato.

A direção para releases é preparar um artefato identificado por conteúdo,
validá-lo e fazer todos os workers da bateria carregarem esse artefato. Copiar
o diretório, usar um nome contendo hash, marcar arquivos como somente leitura
ou manter um ZIP não assegura imutabilidade contra processos do mesmo usuário.
A proteção precisa ser efetiva no SO, ou a aplicação deve continuar verificando
o conteúdo. O fingerprint também deve declarar seu escopo: pacote, Python,
dependências, plugins e configuração, sem confundir esses componentes.

## Critérios antes da mudança de formato

- Um segundo runner continua impedido pelo lock; perda de processo preserva a
  marcação anterior aos efeitos, e um caso interrompido não é repetido sozinho.
- Falhas de gravação, espaço ou publicação interrompem a execução antes dos
  efeitos seguintes. Evidências são publicadas antes do status final; um arquivo
  órfão após crash não promove o caso a verificado.
- Parada continua monotônica dentro da geração correta; pausa não apaga parada.
  Retomada e priorização preservam casos concluídos e sua evidência.
- App, CLI e subprocesso consultam o mesmo estado; detalhes não são perdidos pela
  compactação. Corrupção ou evidência indisponível gera diagnóstico explícito.
- Links/reparse e arquivos auxiliares criados pelo banco recebem revisão de
  paths/permissões. SQLite não cria uma sandbox para workers do mesmo usuário.
- Comparar 25, 100, 250 e, antes da promoção, 1.000 casos com dois commits duráveis
  por caso. Medir escrita, latência de acompanhamento e recuperação. Separar
  inicialização, execução de tarefa e verificação da medição de persistência.
- Simular encerramento de processo entre marcação, execução, verificação,
  publicação de evidência e commit. Isso comprova recuperação de processo; uma
  queda real de energia continua dependendo das garantias do SO e armazenamento.

## Correções realizadas durante a investigação

A suíte da fila expôs regressões da rodada anterior: a compactação eliminou
detalhes do retorno de casos pequenos, e o intervalo de consulta de parada
atrasou o sinal emitido pelo mesmo objeto. Foi extraído
`application/test_queue_evidence.py`, responsável por ler os caminhos canônicos
de evidência para scheduler e inspeção. O estado salvo permanece compacto; o
retorno direto recupera detalhes pequenos, com limite de 64 KiB por evidência.
Resultados maiores mantêm o apontador e continuam acessíveis pela consulta
explícita de detalhes. O serviço de app/CLI solicita apenas o retorno compacto.

Uma sinalização local por geração torna a parada do mesmo objeto imediatamente
observável após sua publicação durável. Sinais externos continuam sendo
consultados em intervalos de até 250 ms, sujeitos ao agendamento e I/O. Nenhum
backend ou formato novo foi ativado por essas correções.

Validação: **72 testes offline passaram**, incluindo scheduler, serviço
compartilhado, UX de fila e runtime real com fronteira de modelo simulada. Lint
passou; mypy passou para os quatro módulos de produção alterados. Nenhuma
chamada a provider foi feita. A primeira
tentativa no sandbox falhou nas permissões de temporários; a rodada válida foi
executada fora dele. [XML local da rodada final](../../../.tmp/queue-strategies-20261102/tests-final.xml).

## Medições locais e limites da evidência

Experimento offline no Windows, Python 3.12.10 e SQLite 3.49.1. A inicialização
dos stores foi excluída do cronômetro. Foram feitos dois commits por caso: início
e resultado compacto. JSON usou o writer atômico real do projeto; o protótipo
SQLite atualizou caso e tempo da fila em transação DELETE/EXTRA. O journal
experimental acrescentou eventos com `flush` e `fsync`, e seu replay conferiu
sequência e estados finais. Ele ainda não implementa recuperação de crash.

| Casos | JSON atômico: mediana de 3 rodadas | SQLite DELETE/EXTRA: mediana de 3 rodadas | JSONL: 1 rodada exploratória |
| --- | ---: | ---: | ---: |
| 25 | 1,75 s | 5,79 s | 1,38 s |
| 100 | 11,10 s | 25,82 s | 6,14 s |
| 250 | 26,01 s | 68,78 s | 14,71 s |

Em 250 casos, os payloads JSON somaram 36.751.980 bytes publicados nas sucessivas
gravações, enquanto o journal final continha 83.494 bytes. Esses valores não são
uma medição dos bytes físicos escritos pelo disco e não incluem metadados do
sistema de arquivos. O replay do journal de 250 casos levou cerca de 0,0022 s.
O resultado SQLite, aproximadamente 2,6 vezes mais lento nessa escala, mudou a
recomendação inicial: normalizar a persistência é interessante, mas trocar o
writer por SQLite não se justificou como otimização de velocidade local.

O hash atual percorreu 1.016 arquivos, com 5.839.052 bytes de conteúdo, em mediana
de 0,189 s, entre sete rodadas com cache aquecido. Isso equivale a aproximadamente
0,378 s por caso com duas conferências, sem contar as verificações iniciais.

| Runtime experimental | Mediana do hash: 7 rodadas | Mediana de importação em subprocesso: 3 rodadas | Soma calculada de importação + 2 hashes |
| --- | ---: | ---: | ---: |
| Diretório atual | 0,189 s | 0,632 s | 1,011 s |
| ZIP com fontes, sem bytecode | 0,004 s | 1,439 s | 1,447 s |
| ZIP com fontes e bytecode CHECKED_HASH | 0,011 s | 0,757 s | 0,780 s |

A última coluna é uma estimativa calculada, não uma execução completa de caso.
O ZIP com bytecode tem cerca de 13,7 MB e cache tag `cpython-312`; o tempo de
construção do snapshot está fora dessa conta e precisa ser amortizado pela
bateria. Importação/configuração foram verificadas, mas plugins, execução de
tarefa, recuperação e proteção do artefato ainda não foram certificados.

Os protótipos não implementam o schema completo de produção. A comparação é
sequencial, sem controlar antivírus, carga do sistema ou cache frio. Os resultados
orientam a próxima implementação; não certificam durabilidade em queda de
energia nem ganho de desempenho do produto inteiro. A escala de 1.000 casos
ainda não foi medida. Repetir a comparação depois dos testes de falha é condição
para promover qualquer formato.

Evidências locais: [persistência e hash atual](../../../.tmp/queue-strategies-20261102/results.json),
[journal e ZIP de fontes](../../../.tmp/queue-strategies-20261102/alternatives-results.json),
[ZIP com bytecode](../../../.tmp/queue-strategies-20261102/archive-pyc-results.json).
Os scripts correspondentes ficam na mesma pasta, sem alterar o runtime ativo.

## Ordem recomendada após as medições

1. Estabilizar os contratos de retorno, parada e evidência — correções acima
   verificadas pelos testes existentes e pela ampliação do teste de retomada.
2. O snapshot JSON compacto reduziu bytes e tempo sem mudar a recuperação. A
   medição determinística de 100 casos confirmou que as gravações pesam na fila
   isolada, mas não dominam uma bateria com o agente real. Deixar journal
   condicionado a um caso de uso em que a fila isolada seja o gargalo: ele
   exige sequência, detecção de dano e recuperação conservadora, além de mudar
   como o Workbench acompanha `state.json`.
3. O ZIP com fontes/bytecode foi testado com o worker completo e verificador;
   passou funcionalmente, mas perdeu tempo para o checkout. Não adotar.
4. Considerar SQLite quando transações/consultas justificarem o custo observado,
   ou se medições posteriores do workload completo mostrarem benefício. WAL
   depende de versão corrigida e política de operação/backup definida.
5. Projetar migração e, posteriormente, proteção efetiva do snapshot no SO. Uma
   validação única do runtime só passa a fazer sentido quando o artefato não
   puder mudar durante a bateria sob o modelo de ameaça escolhido.

## Seguimento: gravação compacta do estado

Antes de introduzir um segundo formato, `write_json_atomic` ganhou a opção
`compact`; a fila usa essa opção para `state.json`. Ela preserva o arquivo
temporário, `flush`, `fsync` e substituição atômica. Formatação compacta corta
whitespace sem alterar o JSON lido por `snapshot()`.

Um par de três rodadas alternadas, sem a suíte concorrendo, comparou 250 casos
sintéticos e 500 commits por rodada. A mediana caiu de **29,81 s para 25,32 s**
(15,1%); o volume de payload JSON publicado caiu de **36.782.482 para 22.255.482
bytes** (39,5%). Os bytes são a soma dos tamanhos do snapshot em cada gravação,
com uma consulta de tamanho após cada publicação; isso adiciona o mesmo custo de
metadados às duas variantes. Não são uma observação dos blocos físicos gravados
pelo disco. Uma primeira medição
foi descartada porque aconteceu ao mesmo tempo que os testes e não entrou no
cálculo.

A mudança passou pela suíte offline da fila: **72 testes**, incluindo serviço
compartilhado, UX e execução com modelo simulado. Ruff e mypy passaram nos
módulos alterados. O custo continua crescendo com N²; a melhoria reduz o volume
por gravação, mantém uma única autoridade e não acrescenta replay ou migração.
Essa é a primeira etapa aplicada; o journal permanece condicionado ao perfil de
baterias maiores.

Resultado bruto: [pares de 250 casos](../../../.tmp/queue-strategies-20261102/compact-confirm-results.json).

## Seguimento: perfil ponta a ponta e candidatos

Uma bateria offline com cinco tarefas reais da fila, runtime canônico,
verificador externo e gateway roteirizado verificou **5/5 casos** em **17,32 s**.
Os workers isolados consumiram **14,93 s**; as 12 verificações de identidade
consumiram **2,04 s**; os 13 snapshots atômicos, **0,37 s**; o verificador,
**0,005 s**. O processo isolado domina a execução real. Isso preserva o
isolamento por caso e mostra que o custo de fila observado no benchmark de
100 casos não deve ser extrapolado para uma bateria com agente real.

No pipeline determinístico de 100 casos, com o `TestQueue` e o verificador real,
todos os casos foram verificados em **14,88 s**. As **203 gravações atômicas**
consumiram **8,78 s** (59% do tempo da fila), e os verificadores, **0,105 s**.
Separadamente, calcular a identidade do runtime consumiu mediana de **0,169 s**
por chamada em 1.016 arquivos. Variantes com `hashlib.file_digest`, 4/8 threads
ou `os.walk` mantiveram a mesma identidade criptográfica, mas foram mais lentas
(medianas de 0,173 s, 0,201 s, 0,218 s e 0,183 s); por isso não foram adotadas.

Também foi testado um ZIP de fontes com bytecode `CHECKED_HASH`, usado tanto
para o hash quanto para iniciar o worker. Os cinco casos foram verificados,
mas a bateria levou **20,28 s** incluindo **2,26 s** para montar o artefato,
contra **17,32 s** no checkout. O hash por chamada caiu para 0,008 s, mas os
workers subiram de 14,93 s para **17,21 s**. Resultado: não migrar para ZIP.

O caminho decision-mode do coding workflow também revelou uma falha de
precondição: edits não recebiam `base_hash` e `expected_text` do snapshot
observado. A proposta agora vincula essas precondições em runtime, como o fluxo
legado já fazia. A fixture `WM-JSON` passou a propor um edit mínimo, coerente
com a política de mudanças. **13 testes offline passaram**, incluindo os dois
testes de integração da fila e o teste novo do vínculo ao snapshot.

O journal segue como opção apenas para baterias grandes sem agente real. O
protótipo anterior sugere economia de gravação, mas não cobre ainda recuperação
de cauda parcial, corrupção, versão de schema e leitura concorrente pelo
Workbench. Como o formato atual apresenta ao usuário um `state.json` sempre
atualizado, o custo e o impacto de um journal completo precisam ser avaliados
antes de implementar outra autoridade de estado.

Evidências: [pipeline de 100 casos](../../../.tmp/queue-strategies-20261102/queue-pipeline-results.json),
[hashes equivalentes](../../../.tmp/queue-strategies-20261102/runtime-hash-results.json),
[worker do checkout](../../../.tmp/queue-strategies-20261102/e2e-offline-results.json)
e [worker do ZIP](../../../.tmp/queue-strategies-20261102/e2e-archive-results.json).

## Perfil aprofundado: traces e trabalho por caso

Repeti a bateria offline de três casos com gateway roteirizado, verificador real
e identidade já calculada sendo reutilizada pelo executor. Duas variantes
alternadas passaram com **3/3 casos verificados**: observabilidade normal e um
controle diagnóstico que desliga a sessão de trace no subprocesso. O controle
não é uma mudança de produto; serve apenas para estimar o custo de observação.

| Rodada | Trace normal | Controle sem trace | Diferença por caso |
| --- | ---: | ---: | ---: |
| Normal antes do controle | 11,20 s | 8,55 s | 0,88 s |
| Controle antes do normal | 10,65 s | 8,62 s | 0,68 s |

Nos perfis sem `cProfile`, o worker normal publica **14 a 15 snapshots atômicos
de metadados por caso**, além de aproximadamente 58 envelopes de evento e dois
updates do índice. Desligar o trace no controle baixou a mediana do worker de
2,22 s para 1,59 s e a mediana de `execute_submission` de 1,59 s para 0,89 s.
O fechamento do trace esperou cerca de 0,17 s por caso; as publicações também
ocupam o writer em background. Os tempos somados do writer podem se sobrepor ao
tempo principal e não devem ser somados como parcelas independentes.

O gateway da fixture respondeu às oito chamadas de cada caso em cerca de 1 ms
no total. Isso mede somente o custo local com respostas roteirizadas; não mede
latência real de servidor, que pode alterar a ordem dos gargalos.

Cada caso ainda calcula duas identidades completas do pacote, com mediana de
0,18 s por hash, para detectar alteração antes e depois da execução. A chamada
redundante no construtor do executor foi removida reutilizando a identidade
confirmada pelo serviço. Reduzir as duas verificações restantes depende de um
runtime imutável com garantia adequada; cache por metadados não oferece a mesma
verificação de conteúdo.

A otimização foi implementada no writer do trace: envelopes continuam sendo
acrescentados ao JSONL assim que processados; apenas snapshots atômicos de
metadados intermediários são agrupados. O padrão é **50 ms**. `flush` captura
uma fronteira de revisão e sequência e força a publicação dessa fronteira; o
fechamento publica o estado terminal e atualiza o índice sem esperar a janela.
A recuperação também adquire um lock exclusivo por trace, valida o JSONL antes
de retomar um trace ativo, reconcilia metadados atrasados e recusa cauda parcial
ou sequências aceitas que não existam no arquivo.

A comparação usou quatro janelas, três tarefas WM-JSON por bateria, gateway
roteirizado e verificação real. **Todas as baterias ficaram 3/3 verificadas.**
Cada duração é o total da bateria e o número de snapshots inclui os três casos.
As repetições de 50 e 100 ms variaram pouco e ficaram próximas em duração; 100 ms
reduziu mais alguns snapshots, mas atrasaria mais a atualização visível. Mantive
50 ms como equilíbrio entre atualização frequente e menos escrita.

| Janela | Duração da bateria | Snapshots de metadados | Tempo em escrita de metadados |
| --- | ---: | ---: | ---: |
| 0 ms | 16,82 s | 138 | 9,30 s |
| 25 ms | 10,67 s | 31 | 2,18 s |
| 50 ms | 10,69 s; repetições: 10,00 s e 10,55 s | 29; 27 e 27 | 2,01 s; 1,72 s e 2,00 s |
| 100 ms | 10,12 s; repetições: 10,28 s e 10,58 s | 22 em cada rodada | 1,31 s; 1,52 s e 1,81 s |

Os dados são microbenchmarks locais; não representam latência de provedor e não
medem bytes físicos no dispositivo. A diferença de duração média entre 50 e
100 ms é pequena para esta amostra. A escolha de 50 ms privilegia frescor da
visão ativa, enquanto o corte de snapshots já é grande frente à janela zero.

Evidências: [0 ms](../../../.tmp/queue-strategies-20261102/e2e-profile-worker-trace-0.json),
[25 ms](../../../.tmp/queue-strategies-20261102/e2e-profile-worker-trace-0p025.json),
[50 ms](../../../.tmp/queue-strategies-20261102/e2e-profile-worker-trace-0p05.json),
[repetições de 50 ms](../../../.tmp/queue-strategies-20261102/e2e-profile-worker-trace-0p05-a.json),
[segunda repetição de 50 ms](../../../.tmp/queue-strategies-20261102/e2e-profile-worker-trace-0p05-b.json),
[100 ms](../../../.tmp/queue-strategies-20261102/e2e-profile-worker-trace-0p1.json) e
[repetições de 100 ms](../../../.tmp/queue-strategies-20261102/e2e-profile-worker-trace-0p1-a.json) e
[segunda repetição de 100 ms](../../../.tmp/queue-strategies-20261102/e2e-profile-worker-trace-0p1-b.json).
O perfil anterior permanece disponível: [A/B com traces](../../../.tmp/queue-strategies-20261102/e2e-profile-worker-trace-results.json),
[A/B sem traces](../../../.tmp/queue-strategies-20261102/e2e-profile-worker-no-trace-results.json) e
[chamadas e `cProfile`](../../../.tmp/queue-strategies-20261102/e2e-profile-worker-deep-results.json).
