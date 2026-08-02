# ADR 0004: `invocation_id` obrigatório no protocolo stdio 1.0

- Status: aceito
- Data: 2026-08-01

## Contexto

O adapter stdio já enviava um `invocation_id` em cada request, mas aceitava uma
resposta sem esse campo. Isso permitia que uma resposta terminal fosse aceita
sem prova de que pertencia à chamada que a aguardava. O mesmo workspace contém
as únicas extensions e testes atuais; não há consumidor externo nem compromisso
de compatibilidade que exija preservar esse comportamento permissivo.

## Decisão

Manter a versão de protocolo atual, `1.0`, e tornar `invocation_id` obrigatório
em toda resposta associada a uma invocação. O adapter stdio:

1. aceita somente o ID não vazio exatamente igual ao enviado no request;
2. rejeita ausência, valor vazio, ID divergente ou desconhecido como
   `ToolStatus.PROTOCOL_ERROR`;
3. rejeita framing com mais de uma linha JSON, incluindo uma segunda resposta
   terminal;
4. não possui fallback para respostas sem ID e não introduz protocolo `1.1`.

A extension de referência ecoa o ID recebido em respostas de sucesso e falha.
Eventos do gateway e o registro de resultado mantêm o mesmo ID. Como cada
invocação stdio usa um subprocesso próprio, timeout encerra esse processo e uma
resposta tardia é descartada, nunca associada a outra chamada.

## Consequências e pendências

- Extensions existentes sem ID precisam ser atualizadas antes de serem usadas.
- A correlação deixa de depender de heurística ou ordem de chegada.
- O contrato de artifacts continua separado e ainda não define seu próprio
  envelope de `invocation_id`.
- Retry ainda não possui `attempt_id`; se for necessário distinguir tentativas
  de uma invocação lógica, isso será uma decisão posterior.
