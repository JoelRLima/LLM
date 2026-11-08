# Correções da auditoria integral — 02/11/2026

Este registro acompanha o [relatório original](relatorio-auditoria-integral-diretorio-20261102.md).
As correções abrangem núcleo, app, CLI, plugins, observabilidade, filas, testes,
ferramentas do repositório e documentação. Não houve chamada de LLM, instalação
de dependências, publicação, alteração de grants automática ou remoção de estado.

## Tratamento dos dez achados

| Achado | Entrega | Limite restante |
| --- | --- | --- |
| 1. Baseline do checkout | `scripts/repository_checkpoint.py` gera hashes, estado Git e cópia opcional das fontes. Checkpoint anterior preservado em `.tmp/audit-fixes-20261102-before`, com 1.955 entradas e SHA-256 `f4d7de8455e40a58195b176dce8083e4107f87787e1028f1d58ed85b67a8d0ac`. | A cópia é da allowlist de código/docs; não abrange estado privado, protótipos ou diretórios negados. As mudanças anteriores continuam no Git, sem atribuição ou commits fabricados. |
| 2. Confiança em plugins | Gerenciador mostra argv, diretório, grants, usuário do SO, escopo do fingerprint e ausência de sandbox; pede revisão antes de autorizar. O backend recusa revisão indisponível ou manifest divergente. | O fingerprint fixa o manifest. Não fixa executável/dependências, nem confina acessos do processo no SO. |
| 3. Privacidade | Redaction de logs após formatação, incluindo exceções/argumentos; padrões de JSON, credenciais citadas, chaves conhecidas, URL e PEM. Rotação limitada. Saída humana de filas/worker também redigida. Export `--metadata-only` sem payloads/notas/snapshot de conteúdo. | Não há anonimização geral de documentos, memória, histórico, seed, checkpoints ou evidências anteriores. [Inventário de dados](../../guides/privacidade-dados-locais.md). |
| 4. Evidência de modelo | Preservada a distinção entre testes offline e qualidade real de modelo. | Epoch live não executado nesta rodada, mantendo a preferência anterior de verificar sem chamar modelo. Uma medição real ainda precisa de perfil/endpoint identificado e orçamento definido. Não foi promovido perfil compacto com evidência sintética. |
| 5. Ciclo de interação | `llm-agent`, `chat` e `local` agora apresentam `InteractiveSession`, como o WebView2. Prompt continua disponível para estado, aprovação, cancelamento e fila. | Chat anterior fica em `chat --legacy-session`, com testes de compatibilidade; Tk permanece técnico/experimental. [Matriz](../../guides/paridade-app-cli.md). |
| 6. Primeiro uso | README reduzido a entrada por objetivo e três passos; conteúdo expandido preservado em guia. `/llm` faz parte do catálogo e é executado no núcleo. | Avaliação observacional com usuários ainda é necessária para medir facilidade; clareza documental não prova usabilidade. |
| 7. Evidência para avaliação | `queue draft` prepara objetivo/oráculos fornecidos pelo revisor e registra SHA-256 da evidência. Preview padrão, escrita explícita em diretório novo, sem gerar gabarito com LLM. | Fixtures sanitizadas devem ser fornecidas pelo autor antes da importação. A ferramenta não concede aprovação humana por conta própria. |
| 8. Bateria independente | `queue run --live --detach` e opção equivalente no app. Reutiliza a fachada da fila, lock do SO, identidade conferida, pausa/parada e recuperação. Processo sem stdin nem janela de console herdada. | `starting` não comprova início/conclusão; consultar status/log. Não é serviço com reinício automático. Interrompidas continuam exigindo revisão. |
| 9. Streaming | Long poll drena o canal da sessão a cada até 50 ms durante tarefa ativa; mantém espera longa quando ocioso e limites de eventos/texto. | O teste cobre atraso local da bridge, não uma promessa de latência ponta a ponta de provider/rede/renderização. |
| 10. Entrega e raiz | Inventário de diretórios e limpeza opcional com allowlist de três caches; proteção de instalações, evidências, estado e links/reparse. | O manifesto de release já existia com Python, wheel, locks e hashes. Essa observação corrige a hipótese inicial da auditoria; não foi necessário criar outro manifesto. |

## Organização e modularidade

- Fila: parser, apresentação das operações, serviço, scheduler e lançamento do
  worker têm responsabilidades distintas. App e CLI chamam a mesma fachada.
- `/llm`: catálogo e execução ficam no núcleo interativo; as interfaces não
  mantêm implementações diferentes de ativação/status/conectividade.
- Redaction de texto tem padrões compartilhados, reutilizados por logs e
  projeções. Export de metadados usa outro nome de arquivo para não fingir uma
  trace reproduzível.
- O terminal não cria um segundo executor de tarefas: bombeia os futures e
  canais do controlador existente no loop da apresentação.
- Foram corrigidos também cinco erros de tipos em dois scripts já existentes,
  descobertos na validação completa. O detector de arquitetura passou a tratar
  fim de nó AST ausente como falha, preservando a verificação.

## Validação e evidência

A suíte consolidada de regressões offline passou com **240 testes**. Uma rodada
posterior de **104 testes** passou após as extrações finais da fila e do CLI,
incluindo a última proteção de saída do worker. As rodadas têm testes em comum;
esses números não devem ser somados como cobertura distinta. Os testes
cobrem revisão/fingerprint de plugins, filtragem do ambiente, acesso controlado
fora do workspace, logs/exceções, export sem conteúdo, comandos compartilhados,
fila, compatibilidade do chat anterior, parser, drafts e proteção da limpeza.
Um subprocesso determinístico comprova que o worker continua após o processo
lançador encerrar. Nenhum desses resultados mede qualidade de uma LLM.

O mypy completo passou para Windows e Linux, com **1.125 arquivos**. A arquitetura
CURRENT passou com **1.011 módulos e 3.574 arestas** na rodada registrada. Lint,
complexidade dos arquivos alterados e sintaxe dos scripts JavaScript receberam
checagens específicas. Os XMLs e logs locais ficam em `.tmp/audit-fixes-*`.

O gate geral `scripts/check_quality.py` **não está aprovado**: encontra dívida
preexistente de complexidade/baseline e arquivos históricos com BOM. A execução
registrou 724 linhas de falha: 654 links estavam somente em cópias de instalação
em `.workbench-managed`, e 28 registros de encoding incluíam evidências antigas.
Essa execução começou antes das últimas extrações de funções e ainda listava
três ocorrências de complexidade que foram corrigidas e rechecadas depois.
Todos os arquivos alterados passaram no lint/C901 específico, sem ampliar
baseline, excluir evidência ou apagar histórico. Os links dos documentos
alterados foram conferidos sem erros. O log completo é
`.tmp/audit-fixes-quality-final.log`; ele não certifica promoção de release.

JavaScript passou na checagem de sintaxe pelo runtime Node disponível. O runner
Edge já existente no projeto verificou plugins e baterias com bridge sintética
em três combinações de tema/tamanho por fixture. As seis verificações passaram,
incluindo recusa/confirmação da revisão de confiança, clique duplicado,
preservação de rascunho e pausa/retomada. Esse teste de DOM não exercita o host
nativo WebView2 nem substitui teste com provider real. Resultados e screenshots
ficam em `.tmp/audit-fixes-browser-plugins` e `.tmp/audit-fixes-browser-queues`.
Uma terceira fixture em `.tmp/audit-fixes-browser-queues-background` passou nos
três tamanhos/temas, verificando especificamente lançamento independente sem
duplicação, interface livre e parada após reabrir o painel. Foram inspecionados
também screenshots dos painéis; a instrução antiga para manter o app aberto foi
atualizada para explicar a opção independente.

O checkpoint final está em `.tmp/audit-fixes-20261102-after`, contendo manifesto
e cópia das fontes. Ele e a comparação com a cópia inicial permitem revisar
esta rodada sem alterar o índice Git ou atribuir mudanças anteriores.

## Pendências antes de promover uma release

1. Revisar e agrupar a grande alteração Git anterior, usando os checkpoints e
   os hashes da rodada para identificar o candidato exato.
2. Tratar as dívidas do gate geral com decisões próprias para código corrente
   e evidência histórica, mantendo seus bytes e proveniência quando necessário.
3. Executar inspeção visual e os fluxos do WebView2 em ambiente de desktop.
4. Planejar um epoch de modelo real com endpoint/modelo identificados, oráculos
   independentes e orçamento limitado; a rodada atual permaneceu offline.
5. Se executar plugins hostis for um requisito, implementar e comprovar uma
   fronteira de SO separada. O perfil atual é explicitamente de código confiável.
