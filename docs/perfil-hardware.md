# Perfil de hardware limitado — GTX 1070, 8 GB de VRAM

## Objetivo

O perfil `low_vram_8gb` é o default do projeto. Ele evita que multitarefa e
reparo disparem várias gerações simultâneas ou carreguem componentes de ML sem
necessidade.

## Defaults

| Limite | Valor | Motivo |
| :--- | ---: | :--- |
| janela de contexto lógica | 8192 tokens | reduz KV cache e compressões tardias |
| saída padrão | 2048 tokens | suficiente para planos e ChangeSets pequenos |
| chamadas de modelo concorrentes | 1 | não duplica modelo/KV cache na VRAM |
| operações de I/O concorrentes | 2 | melhora leituras sem pressionar o modelo |
| processos de validação concorrentes | 1 | reduz pico de RAM/CPU |
| chamadas de modelo por tarefa | 20 | impede grafos ou retries sem limite |
| tentativas de reparo | 2 | limita latência e consumo |
| memória semântica | desabilitada por default | evita stack ML e modelo de embeddings |

Os limites de contexto, saída, concorrência e reparo vêm de
`agent/runtime/hardware.py`; o orçamento de 20 chamadas vem de `config.py`.
`config.json` pode sobrescrever limites operacionais, mas
`max_model_concurrency: 1` é o valor recomendado para essa placa.

## Instalação

Para o runtime leve:

```bash
pip install -e .
```

Para desenvolvimento:

```bash
pip install -e ".[dev]"
```

O extra `ml` (`pip install -e ".[ml]"`) é opcional. O agente usa busca lexical, nomes de arquivo,
símbolos e hashes mesmo sem NumPy ou sentence-transformers.

## Comportamento do runtime

- `ContextManager` usa o limite do perfil para compressão e orçamento de saída.
- retries do cliente legado também respeitam o teto de saída do perfil.
- `PlanExecutor` limita o lote de leituras por `max_io_concurrency`.
- `TaskExecutionContext` compartilha um `ModelConcurrencyGate` entre pai e
  filhos.
- orçamento de chamadas e gate de processos também são compartilhados entre
  pai e filhos.
- `TaskGraphScheduler` pode executar tarefas de leitura/CPU em paralelo, mas
  recursos de escrita conflitantes são serializados.
- workflows ranqueiam targets, diretórios, símbolos, nomes e imports, limitam a
  seleção a seis arquivos e respeitam o orçamento de texto do perfil;
- comandos `/code` e templates evitam chamadas de planejamento para operações
  conhecidas;
- edições estruturadas enviam e regeneram trechos pequenos em vez do arquivo
  inteiro quando o provider segue o schema;
- análise Python usa `ast` da biblioteca padrão; não carrega modelo na GPU.

## Ajustes seguros

Se houver falta de memória ou lentidão:

1. reduza `model_profiles.<nome>.max_tokens`;
2. mantenha `max_model_concurrency` em 1;
3. reduza o contexto configurado no servidor local;
4. mantenha `max_io_concurrency` em 1 ou 2;
5. deixe memória semântica desativada;
6. informe `targets` específicos ao `code_task`;
7. evite `include_tests: true` para cada mudança pequena; use-o no gate final.
8. prefira `/code` e templates determinísticos para tarefas recorrentes;
9. mantenha `code_policy` conservadora: confiança não reduz consumo de VRAM,
   mas evita gastar validação/reparo em propostas arriscadas.

Se houver folga de CPU/RAM, aumentar I/O para 3 ou 4 não aumenta diretamente a
VRAM. Aumentar concorrência de modelo pode aumentar bastante o consumo e não é
recomendado para o perfil.

## O que o projeto não faz

- não escolhe ou baixa automaticamente um modelo;
- não instala dependências para executar testes;
- não mede VRAM por uma dependência CUDA obrigatória;
- não executa vários modelos locais simultaneamente;
- não garante sandbox de sistema operacional para código não confiável.

## Verificação

Os limites do perfil e o compartilhamento de contexto são cobertos por
`tests/unit/runtime/test_runtime_context.py`. Concorrência e conflitos são cobertos por
`tests/unit/planning/test_task_graph.py`.
Seleção de contexto, templates e política de confiança são cobertos por
`tests/unit/code/test_code_assistance.py`.
