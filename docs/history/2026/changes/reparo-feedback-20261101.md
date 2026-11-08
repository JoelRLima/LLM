# Reparo estruturado com feedback específico

Entrega selecionada pela [pesquisa ampla de prioridades](../../../research/2026/pesquisa-ampla-prioridades-20261101.md), com fundamento nas orientações de feedback acionável e avaliação por trajetória.

## Lacuna reproduzida

O caminho canônico `ContextManager → run_model_call → resolve_model_decision` autorizava um retry limitado e reajustava seu orçamento, mas repetia o prompt sem informar por que a resposta não havia sido admitida. O pacote anterior `e1f222af37ba-20261102T011032Z-e675b8bc` reproduziu esse comportamento em episódios sintéticos de JSON inválido, contrato inválido e término por limite de saída.

## Mudança

Um objeto restrito à chamada registra uma nota curta, composta apenas de texto do harness e do identificador canônico do contrato:

- `MODEL_JSON_INVALID`: não foi obtido um JSON completo e não ambíguo;
- `MODEL_DECISION_INVALID`: JSON não corresponde à decisão solicitada;
- `MODEL_OUTPUT_INCOMPLETE`: saída interrompida por limite.

A nota é acrescentada à solicitação original somente na preparação do retry. Ela não copia texto da resposta rejeitada, não reescreve argumentos de ferramenta e não inventa o formato de um contrato desconhecido. O caminho inicial mantém o mesmo prompt focal.

O ajuste de contexto e sua medição são executados de novo com a nota incluída como parte obrigatória da solicitação. Se não couber, o despacho é bloqueado pelo erro de contexto existente; não se remove evidência obrigatória para abrir espaço. Esse é um custo real da mudança: um retry próximo do limite pode deixar de caber. Nos episódios comparados, a nota teve 431 a 455 caracteres, abaixo do limite de 900 caracteres coberto pelos testes.

A autorização de reparo, o limite de chamadas, a admissão de decisão, a gramática/schema e a compatibilidade de reasoning permanecem nos mesmos proprietários. Resposta filtrada não é repetida. Segundo resultado inválido encerra o reparo e preserva o diagnóstico inicial e final. O objeto de feedback não é compartilhado com uma próxima tarefa/chamada, e as mensagens originais da sessão são restauradas.

## Comparação independente

```powershell
.venv/Scripts/python.exe scripts/benchmark_decision_repair.py --reference-python .workbench-managed/versions/e1f222af37ba-20261102T011032Z-e675b8bc/runtime/Scripts/python.exe --output reports/decision-repair-comparison-20261101.json
```

[Resultado completo](../../../../reports/decision-repair-comparison-20261101.json). Cada runtime executou sete episódios: resposta válida inicial, JSON inválido, contrato inválido, truncamento, filtro, segundo erro e esgotamento de chamadas. Foram preservados resultado, número de chamadas ao provider simulado e número de chamadas no ledger. O novo comportamento observado é o feedback somente nos retries autorizados que chegaram ao provider; não houve uma chamada adicional.

As respostas foram pré-definidas, independentemente da nota. Portanto, a comparação **não demonstra aumento da taxa de recuperação de modelos**, redução de custo faturado ou ganho de latência. Ela verifica mudança no pedido enviado e preservação dos gates nos casos cobertos.

## Validação

- Planejamento, LLM, rota compacta e código: **1.614 aprovados, cinco ignorados**. [JUnit](../../../../reports/research-repair-tests-20261101.xml).
- Interfaces e integração: **724 aprovados, 16 ignorados**, com o aviso Tk de descarte fora da thread principal já observado nas rodadas anteriores. [JUnit](../../../../reports/research-repair-integration-20261101.xml).
- Conjunto focado final: **17 testes aprovados**, incluindo o caso adicional de JSON Schema nativo. Verifica duplicidade de chaves, não finitos, decisões múltiplas, JSON inválido, contrato inválido, truncamento, filtro, limite de recuperação, limite real de chamadas, overflow de contexto com a nota, GBNF/fallback, JSON Schema, restauração de mensagens e ausência de estado entre chamadas.
- [44 tarefas simuladas](../../../../reports/research-repair-campaign-20261101.json): mesmos resultados, códigos de falha, arquivos alterados, falsos sucessos e efeitos não autorizados da campanha anterior. Chamadas padrão/compacto preservadas em **171/173**. Tokens contabilizados: **617.504 → 617.611** e **626.788 → 626.895**; acréscimo de 107 por perfil, não cobrança real.
- Ruff, complexidade até dez e Mypy dos módulos/script passaram. A análise estática foi verificada com plataformas Windows e Linux; não houve execução em Linux.
- Arquitetura CURRENT aprovada: 982 módulos e 3.470 arestas, sem alterar política ou baseline.

Nenhum endpoint de inferência foi consultado. O ganho semântico esperado da mensagem de erro continua sendo hipótese a avaliar por perfil; os testes cobrem o comportamento do harness.

## Instalação verificada

Instalada `724c6ab37d11-20261102T014056Z-78bf3553`, preservando `e1f222af37ba-20261102T011032Z-e675b8bc` como anterior. O status confirmou `update_available=false`; o fingerprint do pacote coincide com os metadados, e o fingerprint da campanha coincide com as fontes atuais.

O Python isolado (`-I`) da instalação repetiu os sete episódios e coincidiu integralmente com os resultados da fonte corrigida, sem modelo real. SHA-256 do wheel: `8ccdeb30b3bfb407ce253af6c588f57122b1e807c39bb57421dd269d5739569e`.
