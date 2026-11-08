# Execução compacta e avaliação de modelos menores

O runtime oferece um perfil experimental para reduzir a carga de decisão e o
contexto opcional enviados ao modelo. CLI e Workbench usam a mesma implementação.
Não há dependência nova nem serviço adicional.

No arquivo indicado por `llm-agent config path`, acrescente a propriedade na raiz:

```json
"agent_execution_profile": "compact"
```

`standard` continua sendo o padrão e permite reverter a seleção. Essa propriedade
controla a estratégia de execução; `default_model_profile` escolhe o modelo e o
endpoint. Configurações antigas sem a propriedade continuam válidas.

## Comportamento

Uma tarefa nova de leitura, com objetivo curto, sem efeitos persistentes e com
até duas obrigações, pode seguir diretamente pelo loop reativo existente.
A seleção exige autoridade limitada a leitura e diretiva AUTO/READ com
deliberação economy/normal. Retomadas, planejamento explícito, tarefas com escrita
e deliberação smart/cautious seguem o planejamento normal.

Nas etapas de planejamento inicial, decisão reativa, continuação e resposta
final, o perfil remove registros opcionais de dicas do workspace e histórico
derivado que duplicam o contexto focal. Evidências obrigatórias, instruções do
projeto, memória e fatos do repositório permanecem disponíveis. O ajuste existente
ao limite de contexto continua responsável pelo orçamento final.

O caminho curto reutiliza a execução validada: seleção de ferramentas, argumentos,
authority, aprovações, cancelamento e critérios de conclusão continuam nos seus
responsáveis canônicos. A projeção entre a visão reativa e a visão de execução
valida o mesmo snapshot e preserva exatamente as ferramentas apresentadas.

## Campanha reproduzível

Execute a partir da raiz do checkout:

```powershell
.venv\Scripts\python.exe scripts/run_weak_model_campaign.py --variant both --repetitions 1 --output reports/weak-model-v2-fixture-nova-rodada.json
```

Sem `--live`, as respostas são determinísticas e nenhum endpoint é chamado.
Essa execução testa o encadeamento, as rotas e os verificadores; não mede a
precisão ou a velocidade de um modelo real.

São 22 cenários: sete tarefas práticas, dez casos de bindings, continuação,
falhas e autoridade, resposta direta sem ferramentas, falha parcial e três
transformações de dados. Cada execução usa workspace e configuração isolados. Os
verificadores de dados conferem efeitos nos arquivos independentemente da resposta
final, incluindo preservação dos dados de entrada e ausência de mudanças extras.

Quando o servidor estiver ativo, execute com uma configuração e um perfil reais:

```powershell
.venv\Scripts\python.exe scripts/run_weak_model_campaign.py --live --config config.example.json --profile local_8gb --variant standard --canary --output reports/weak-model-live-canary.json
```

O modo live faz chamadas ao provedor. Ajuste endpoint e nome do modelo no arquivo
antes de usá-lo. Os cenários autorizam automaticamente operações em seus próprios
workspaces temporários.

O relatório registra sucesso verificado, falso sucesso, chamadas, tokens quando
disponíveis, latência, contexto por etapa, identidade observada do provedor e
repetições de ferramentas/argumentos. Repetição não é classificada automaticamente
como loop. Medições ausentes permanecem identificadas; contagens de chamadas do
gateway e do runtime são separadas.

As repetições alternam a ordem standard/compact. Fingerprints identificam fixtures,
expectativas e fontes relevantes antes/depois; mudança durante a campanha invalida
a comparação. `model_used` e `live_model_response_observed` indicam observação de
resposta real, separadamente da seleção do backend.

## Estado da validação

O endpoint local configurado não respondeu à consulta de identificação desta rodada. A campanha offline histórica de 29/10/2026 está em
[`reports/weak-model-fixture-20261029.json`](../reports/weak-model-fixture-20261029.json).
Ela não comprova ganho de precisão, tokens ou latência com modelos menores. O
perfil compacto continua opt-in até a comparação com um modelo real.
## Atualizacao de implementacao - 1/11/2026

O perfil `standard` continua sendo o padrao. `compact` permanece opt-in; esta rodada nao o promoveu nem alterou seus criterios de selecao. A correcao P0 do prompt inicial agora so apresenta `direct_response` e `use_tools`; `continue_after_plan` nao aparece na instrucao. Testes extraem os exemplos JSON anunciados, verificam sua admissao no contrato `INITIAL_PLAN` e confirmam que a forma legada e rejeitada.

O caminho de saida estruturada ganhou schemas nativos somente para os contratos exatos `FINAL_GENERATION` e `SUMMARIZATION`, quando o perfil declara suporte a `JSON_SCHEMA`. Se `GBNF` for declarado, ele continua preferido; um fallback para schema ocorre apos rejeicao GBNF em cache. Contratos de plano, ferramenta reativa e bindings mantem o caminho GBNF ou prompt existente. Schema restringe o formato e nao substitui admissao tipada nem verificacao do resultado.

Os testes offline e checagens estáticas validam esses caminhos de código, não a qualidade de um modelo real. A avaliação v2 foi implementada com 22 casos distintos; a referência simulada passou nos dois modos (44 execuções), registrada em [weak-model-v2-fixture-20261101.json](../reports/weak-model-v2-fixture-20261101.json). Seu gate de qualidade de modelo permanece `inconclusive`, com `MODEL_NOT_RUN`.

O canário usa dez casos e uma repetição em um único modo. Cada caso usa os limites canônicos do runtime, com tetos de 20 chamadas de modelo, 30 passos, 200.000 tokens contabilizados, 30 chamadas de ferramentas e 300 segundos; limites menores da configuração são preservados. O prazo é cooperativo: uma chamada em andamento ainda depende do timeout do provedor. O relatório registra os limites efetivos.

O gate exige aprovação de todos os casos selecionados e identidade concreta observada nas respostas do provedor. Identidade genérica ou ausente, definição de oráculo ausente e mudança nas fontes impedem aprovação. Um caso reprovado reprova o gate; falso sucesso ou efeito não autorizado interrompe novos casos no modo live, preservando a falha e marcando os demais como não executados. O CLI retorna código 2 para resultado live inconclusivo e 1 para falha. Evidências textuais continuam sendo projeções limitadas e sanitizadas; os resumos de segurança usam todo o histórico de invocações antes do recorte de apresentação.

Não há baseline atual de modelo fraco: o perfil `local_8gb` continua apontando para `default`, e a consulta a `/models` em loopback expirou após dois segundos, inclusive fora do sandbox. Nenhuma inferência foi feita. Redução de ruído, mudanças adicionais de compactação e ajustes de recuperação aguardam comparação com modelo identificado.

### Verificação e instalação desta rodada

- Regressão de LLM, planejamento, campanha e rota compacta: 403 testes aprovados.
- Suíte de avaliação: 176 testes aprovados; checagem posterior das alterações finais de avaliação e manutenção: 67 aprovados e um ignorado. Os conjuntos se sobrepõem. Os 14 testes finais do gate e CLI também passaram.
- Ruff e complexidade das novas rotinas: aprovados; módulos da campanha abaixo de 300 linhas, complexidade por função até 10. Mypy dos nove módulos de avaliação/LLM passou para as plataformas estáticas Windows e Linux; três módulos adicionais de CLI/manutenção passaram para Windows. Isso não equivale a execução em Linux.
- Arquitetura CURRENT: aprovada, 976 módulos e 3.443 arestas. A preparação da configuração gerenciada passou para a camada de aplicação, mantendo a migração e validação canônicas.
- Instalação offline `e1e962d5ddee-20261101T184714Z-858d19ec`, versão `0.2.0rc1`: `pip check`, importação isolada e fingerprints aprovados. A verificação independente confirmou pacote íntegro e `update_available=false`; a versão anterior `5bea126ab553-20261101T151334Z-4f267292` foi preservada. Smoke dos contratos no pacote instalado passou. SHA-256 do wheel: `bcd823197d2a89bfede9cc66b67f6b4c5b2526692be7f7e04233e75aa5ecbbaf`.

Reabra o Workbench para carregar a atualização. A rodada não incluiu nova verificação visual da janela nativa nem inferência com modelo real.

### Rodada complementar: admissão inequívoca

Foram corrigidas decisões extraídas de JSON truncado, múltiplas respostas, chaves repetidas e números não finitos. Metadados de término interrompido agora impedem admissão e aprovação de propostas de edição ou de pareceres de verificação. O limite de reparo existente foi mantido. A reprodução contra o pacote anterior, os testes e as fontes estão em [correcoes-admissao-modelos-20261101.md](history/2026/changes/correcoes-admissao-modelos-20261101.md). A nova campanha simulada passou nos 44 casos; o modelo real continua sem medição. A consulta de identificação ao servidor local foi repetida e não obteve conexão.

### Rodada complementar: recuperação e cache de gramática

O reparo limitado agora preserva associações canônicas a requisitos e obrigações sem rejeitá-las como campos extras. Erros de sintaxe de gramática não são mais confundidos com falta de suporte; a degradação exige uma mensagem explícita reconhecida. O cache de aceitação de GBNF só muda em requisições que contêm gramática GBNF não vazia. Isso registra aceitação da requisição, não prova que o servidor ou o modelo obedece semanticamente à gramática. A rodada usou exclusivamente inspeção e testes locais, sem consultar endpoints ou modelos. [Diagnóstico, mudanças e validação](history/2026/changes/correcoes-recuperacao-20261101.md).

### Gargalos e medição repetida de entrada

A investigação de gargalos comparou chamadas semânticas, dimensionamento de contexto e persistência de traces. Foi implementado reaproveitamento volátil de uma medição de tokens entre ajuste de contexto e admissão, limitado ao mesmo objeto e payload, por um uso e até um segundo. Requisições e configurações alteradas são medidas novamente; limites e contabilização permanecem no caminho canônico. O benchmark com HTTP simulado reduziu de 40 para 20 as consultas ao contador/tokenizer em 20 despachos, mantendo todas as conclusões e os tokens contabilizados. Não mede ganho de latência de inferência. [Diagnóstico, alternativas ponderadas e evidências](research/2026/gargalos-agent-20261101.md).

### Renderização de ferramentas

A descoberta agora reutiliza no próprio resultado os textos do índice e dos cartões que já passaram pela validação de tamanho. Métricas e prompt preservam o mesmo conteúdo. Mudanças de limite de contexto ou de seleção exigem renderização e validação novas. Em cinco amostras locais de 100 ciclos, as renderizações detalhadas caíram de 300 para 100, com hashes idênticos de prompt e eventos. A suíte ampla passou com 1.429 testes aprovados e um ignorado; a matriz de 44 tarefas simuladas também passou. [Implementação, comparação e limites](research/2026/contexto-ferramentas-20261101.md).

### Revisão de plano com seleção de ferramentas da mesma tarefa

A fronteira de raciocínio pode reaproveitar uma seleção válida sob o mesmo objetivo, tentativa, tarefa, catálogo, capacidades, autoridade e orçamento. A revisão do modelo e os dados de progresso permanecem novos. Se a revisão pedir uma ferramenta fora da seleção anterior, há uma descoberta normal e uma nova decisão antes de executar, com atualização limitada e orçamento canônico. A comparação local preservou os resultados de 88 tarefas simuladas e as revisões de conclusão: no perfil padrão, 189 chamadas caíram para 171; no compacto, 190 para 173. Isso não mede qualidade ou latência de um modelo real. [Diagnóstico, comportamento, comparação e testes](history/2026/changes/reducao-chamadas-revisao-20261101.md).

### Isolamento do cache de descoberta

O cache de seleções agora tem limite de oito entradas e exige o mesmo escopo de tarefa, autoridade, contexto, apresentação e orçamento. Uma atualização forçada invalida a seleção anterior antes de consultar e só publica outra após validar a apresentação. A reprodução contra o pacote anterior confirmou seleção antiga entre tarefas e após atualização inválida; a correção refaz a descoberta nesses casos. Os 1.466 testes e as 44 tarefas simuladas passaram, preservando as contagens de 171/173 chamadas dos perfis padrão/compacto. [Reprodução, decisões e limites](research/2026/cache-descoberta-20261101.md).

### Rodada ampla: resposta, evidência e recuperação

O adaptador de streaming diferencia término confirmado, EOF sem confirmação e truncamento/filtro. Falhas de transporte ou parsing preservam texto parcial e tokens recebidos para a contabilidade canônica. Obrigações são projetadas como registros JSON, e o reparo de seleção informa posições inválidas sem reinserir texto arbitrário. A regressão foi ampliada para código, interfaces e integração: 2.328 testes passaram e 21 foram ignorados, com um aviso Tk registrado. Também foram corrigidos fixtures de 50 falhas reproduzidas no pacote anterior, sem enfraquecer os oráculos. As 44 tarefas simuladas mantiveram os resultados e as contagens de chamadas. [Diagnóstico, reprodução e limites](research/2026/rodada-ampla-20261101.md).

### Experiência de recuperação no Workbench

Corrigido o ID duplicado que ligava a ação de perfil ao texto do resumo. Resultados são apresentados em português, com cores coerentes com falha e aviso; códigos originais permanecem no evento. Após uma solicitação não concluída, “Revisar pedido” recupera o texto para edição sem reenviar e preserva o rascunho atual dentro dos limites existentes. A recuperação é confinada ao projeto original. Foram aprovados 571 testes e cinco harnesses JavaScript; as verificações de DOM e layout usam Edge headless e bridge sintética, sem modelo real. [Mudanças, provas visuais e limites](history/2026/changes/ux-recuperacao-20261101.md).

### Fila de pedidos sem comandos memorizados

O painel Tarefas mostra os pedidos guardados no PendingStore canônico e oferece Enviar e Remover. O envio exige sessão livre e clique explícito; as ações preservam o rascunho atual. A interface usa os comandos já existentes, com validação de projeto, esquema e respostas atrasadas, sem outro escalonador. A regressão passou em 583 testes, e cinco harnesses JavaScript e Edge headless verificaram os fluxos com dados sintéticos, sem modelo real. [Uso, implementação e limites](research/2026/fila-visual-20261101.md).

### Pesquisa ampla e reparo orientado por feedback

A [consulta a 26 fontes](research/2026/pesquisa-ampla-prioridades-20261101.md) priorizou diagnóstico por etapa, conformidade de runtime, reparo limitado, contexto relevante e uso básico guiado. Foi implementada uma nota curta no retry canônico, indicando JSON inválido, contrato inválido ou truncamento sem reinserir texto rejeitado como instrução. A nota é reajustada no contexto e respeita os limites existentes. A regressão passou em 1.614 testes de runtime/código e 724 de interfaces/integração; os 17 casos focados e as 44 tarefas simuladas também passaram. Não foi medida acurácia de modelo real. [Comparação, custo de contexto, testes e instalação](history/2026/changes/reparo-feedback-20261101.md).

### Diagnóstico observacional de respostas — 02/11/2026

Uma pesquisa de oito fontes primárias resultou em diagnósticos por contrato carregado e sinais observados, nos relatórios de avaliação. O estágio antigo foi identificado como heurístico; contratos sem cobertura de validador ficam não avaliados. Formato válido não comprova sucesso da tarefa. A suíte selecionada passou com 593 testes; os 44 casos simulados preservaram resultados, efeitos, chamadas e contabilidade. [Pesquisa, prioridades e limites](research/2026/pesquisa-diagnostico-20261102.md).
