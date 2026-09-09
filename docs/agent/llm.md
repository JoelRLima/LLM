# LLM e providers

> **STATUS: CURRENT — PRIMARY HOME.** Configuração de uso fica em
> [modelos-providers.md](../modelos-providers.md).

## Contrato

`agent/llm/contracts.py` define `ModelGateway`, mensagens, requests, responses,
stream events, uso e erros. O domínio conversa com essa abstração; detalhes
HTTP pertencem ao provider.

O provider CURRENT é `OpenAICompatibleGateway`. Ele encapsula endpoint
OpenAI-compatible, payloads, `choices`, SSE, tokenização opcional e capabilities
de structured output. `factory.py` resolve um profile de configuração e cria o
gateway. Não há adapter nativo adicional documentado como suportado.

`ResolvedModelProfile` carrega somente a referência opcional de credencial. O
shape V1 aceita `source=env`, `kind=bearer` e um nome de variável; o valor não
entra no profile persistido, em requests, métricas ou eventos. A resolução é
late e exclusiva do `OpenAICompatibleGateway`, imediatamente antes do envio
HTTP. Referência ausente ou vazia falha fechado sem expor o nome ou o valor.

`structured_output.py` negocia JSON Schema, GBNF ou JSON em prompt conforme as
capabilities declaradas e valida a resposta. Gramática reduz erro sintático,
mas não substitui `PlanValidator`, authority ou schema de tools.

Identidade declarada do profile e identidade observada pelo provider permanecem
campos distintos. A observação só é registrada quando o response fornece um
identificador; sucesso textual nunca é usado para inferir o modelo observado.

## Sessão, contexto e roteamento

- `ChatSession` mantém o histórico e constrói `ModelRequest` tipados. As entradas
  `complete_request` e `consume_stream_request` formam a fronteira da sessão e
  delegam o lifecycle compartilhado de chamadas ao `ModelCallService`.
- `ContextManager` monta contexto do projeto e compacta o histórico. A memória
  disponível é serializada como contexto; o caminho atual não seleciona memória
  por um orçamento separado. O budget explícito cobre a saída do modelo e a
  compressão do histórico.
- `ContextManager` resolve decisões estruturadas por meio do
  `ModelCallService`, que coordena o ciclo de `ModelRequest`/`ModelResponse` e
  stream via `ModelGateway`; `structured_output` valida a representação
  estruturada quando o contrato da decisão a exige.
- o router escolhe entre `coder`, `researcher`, `general` e
  `security_auditor`: saudações e pedidos de listagem/consulta têm heurísticas
  determinísticas; keywords de segurança selecionam o auditor e os demais casos
  podem consultar o modelo. Persona limita a view de tools; não cria authority.

## Scripted/offline versus modelo real

Testes e a campanha determinística podem injetar respostas scripted preservando
a application e o gateway reais. Isso prova controle, efeitos e grading de
maneira determinística; não prova robustez de um modelo. `UnavailableModelGateway`
permite falha explícita quando nenhum backend está disponível. Evidência
real-model exige profile, endpoint, modelo e condições registrados e permanece
uma etapa separada da avaliação determinística.

## Wave 15: pressao de contexto

Durante uma decisao de tarefa, `ContextManager` usa `build_execution_frontier`
para inserir uma projecao fresca e limitada do runtime. A projecao e dado nao
confiavel: nao carrega capability, grant, approval ou resource scope.
`decide_context_pressure` reutiliza a medicao canonica do request do provider,
preserva evidencia obrigatoria antes de qualquer historico opcional e so prova
`FULL`/overflow com measurement exato. Measurement inexato produz `COMPACT`
minimo, sem retry de sizing.

A modelagem e request-local. O shape temporario de `session.messages` e
restaurado no `finally`; o caminho W15 de `maybe_compress_context` nao chama o
modelo de resumo nem persiste uma conversa compactada. Reparos estruturados
autorizados continuam sendo uma tentativa independente do owner de recovery e
reentram no fitting canonico.

## Non-guarantees

O repositório não promete portabilidade universal entre APIs que apenas se
autodenominem OpenAI-compatible, disponibilidade de backend, qualidade de
modelo, isolamento de rede nem segredo automático de prompts/relatórios.
