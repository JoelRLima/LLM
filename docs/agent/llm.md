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

`structured_output.py` negocia JSON Schema, GBNF ou JSON em prompt conforme as
capabilities declaradas e valida a resposta. Gramática reduz erro sintático,
mas não substitui `PlanValidator`, authority ou schema de tools.

## Sessão, contexto e roteamento

- `ChatSession` mantém histórico e é também fachada de compatibilidade para o
  fluxo legado.
- `ContextManager` monta contexto do projeto e compacta o histórico. A memória
  disponível é serializada como contexto; o caminho atual não seleciona memória
  por um orçamento separado. O budget explícito cobre a saída do modelo e a
  compressão do histórico.
- `ContextManager` resolve decisões estruturadas via `ModelGateway` e
  `structured_output`; `ModelClient` permanece como fachada de compatibilidade
  que traduz payloads legados e delega a mesma política canônica de decisão.
- o router escolhe entre `coder`, `researcher`, `general` e
  `security_auditor`: saudações e pedidos de listagem/consulta têm heurísticas
  determinísticas; keywords de segurança selecionam o auditor e os demais casos
  podem consultar o modelo. Persona limita a view de tools; não cria authority.

## Scripted/offline versus modelo real

Testes e o Block A podem injetar respostas scripted preservando a application e
o gateway reais. Isso prova controle, efeitos e grading de maneira
determinística; não prova robustez de um modelo. `UnavailableModelGateway`
permite falha explícita quando nenhum backend está disponível. Evidência
real-model exige profile, endpoint, modelo e condições registrados e ainda não
foi concluída no Marco 3 Block B.

## Non-guarantees

O repositório não promete portabilidade universal entre APIs que apenas se
autodenominem OpenAI-compatible, disponibilidade de backend, qualidade de
modelo, isolamento de rede nem segredo automático de prompts/relatórios.
