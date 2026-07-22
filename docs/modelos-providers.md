# Modelos e providers

## Objetivo

O núcleo não depende de um modelo específico nem do formato HTTP de um
provider. A abstração garante intercâmbio de backend; ela não garante que
modelos diferentes tenham a mesma qualidade, janela de contexto ou aderência a
JSON.

## Componentes

| Componente | Responsabilidade |
| :--- | :--- |
| `agent/llm/contracts.py` | mensagens, requests, responses, uso, eventos de stream, capacidades e erros normalizados |
| `agent/llm/providers/factory.py` | seleciona o perfil e cria o adapter |
| `agent/llm/providers/openai_compatible.py` | HTTP, Chat Completions, `choices`, SSE, GBNF, reasoning específico e `/tokenize` |
| `agent/llm/structured_output.py` | escolhe schema nativo, GBNF ou JSON por prompt e valida o retorno |
| `session.py` | histórico da conversa e fachada temporária para consumidores legados |
| `agent/llm/model_client.py` | compatibilidade com o fluxo legado de decisões; casos de uso novos usam `ModelGateway` |

`ChatSession` ainda expõe `build_payload`, `send_request` e `process_stream` para
não quebrar a CLI e o executor legado. Esses métodos delegam ao adapter; o
parsing de protocolo não permanece na sessão.

`UnavailableModelGateway` representa explicitamente a ausência de backend. Ele
permite que análise e review construam um contexto sem modelo e falha fechada se
uma operação tentar gerar conteúdo.

## Contrato normalizado

Um provider implementa `ModelGateway`:

```python
class ModelGateway(Protocol):
    provider_name: str
    capabilities: ProviderCapabilities

    def complete(self, request: ModelRequest) -> ModelResponse: ...
    def stream(self, request: ModelRequest) -> Iterator[StreamEvent]: ...
    def count_tokens(self, text: str) -> int | None: ...
```

O request contém mensagens, modelo lógico, temperatura, limite de saída,
reasoning opcional e solicitação de saída estruturada. O response contém texto,
reasoning, uso normalizado e metadados do provider. Casos de uso não acessam
`choices`, objetos `requests.Response` ou linhas SSE.

## Capacidades

As capacidades são configuradas por perfil:

- `streaming`;
- `structured_output`: `json_schema`, `gbnf` ou `json_prompt`;
- `reasoning`;
- `token_counting`;
- `tool_calls`.

O comportamento nunca deve desviar pelo nome do modelo. A estratégia de saída
estruturada segue esta ordem:

1. JSON Schema nativo, se disponível e houver schema;
2. GBNF, se disponível e houver gramática;
3. JSON instruído no prompt, seguido de parsing e validação runtime.

O fallback não aceita JSON truncado. O parser permite objeto/array completo e
bloco Markdown JSON, depois valida tipos, propriedades obrigatórias, enums,
arrays e propriedades adicionais usadas pelos contratos internos.

## Configuração recomendada

```json
{
  "hardware_profile": "low_vram_8gb",
  "default_model_profile": "local_8gb",
  "model_profiles": {
    "local_8gb": {
      "provider": "openai_compatible",
      "base_url": "http://127.0.0.1:8080/v1",
      "model": "default",
      "temperature": 0.2,
      "max_tokens": 2048,
      "timeout": 300,
      "capabilities": {
        "streaming": true,
        "structured_output": "gbnf",
        "reasoning": true,
        "token_counting": true,
        "tool_calls": false
      },
      "provider_options": {
        "reasoning_mode": "chat_template_kwargs",
        "tokenize_path": "/tokenize"
      }
    }
  }
}
```

Se o servidor não oferecer GBNF, configure `structured_output` como
`json_prompt`. Não anuncie uma capacidade que o endpoint não implementa.

## Compatibilidade legada

As chaves `api_url`, `model`, `temperature`, `max_tokens`, `timeout` e
`ENABLE_GBNF` continuam aceitas. Na ausência de um perfil selecionado, a factory
gera internamente um perfil `legacy` OpenAI-compatible. Código novo não deve
usar essas chaves diretamente.

`carregar_config()` também normaliza tipos, faixas, capacidades e opções
internas dos perfis. Provider desconhecido não é trocado silenciosamente: a
factory o rejeita com erro explícito, permitindo extensões futuras sem manter
uma allowlist no carregador genérico.

## Como adicionar um provider

1. Crie um módulo em `agent/llm/providers/`.
2. Implemente todo o `ModelGateway`.
3. Traduza opções específicas somente no adapter.
4. Normalize erros para `ModelTimeoutError`, `ModelConnectionError`,
   `ModelResponseError` ou `UnsupportedModelCapability`.
5. Registre o nome em `create_model_gateway`.
6. Rode a mesma suíte de contrato usada pelo adapter atual.
7. Documente capacidades verdadeiras e fallbacks.

Não importe o adapter em `agent/code`, `agent/planning`, skills ou workflows.

## Testes relacionados

- `tests/unit/llm/test_model_gateway.py`;
- `tests/unit/llm/test_structured_output.py`;
- `tests/unit/runtime/test_session.py`;
- `tests/unit/llm/test_grammar.py` para a fachada GBNF legada.

## Limitações atuais

- existe um adapter real embutido: OpenAI-compatible;
- a detecção automática de capacidades não substitui configuração explícita;
- o fluxo legado ainda usa `ModelClient`; os workflows novos usam o gateway;
- contagem exata depende do endpoint, caso contrário o runtime usa estimativa;
- trocar provider não elimina diferenças de qualidade entre modelos.
