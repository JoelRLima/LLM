# Preparação de ferramentas sem renderizações repetidas

Rodada complementar de 1º de novembro de 2026. A investigação e a validação foram locais, sem rede e sem inferência.

## Diagnóstico e escolha

O fluxo de descoberta renderizava o mesmo índice e os mesmos cartões detalhados para três consumidores: validação de tamanho, métricas e montagem do prompt. Essa repetição refazia serialização, escaping, projeção de metadados e verificações de schema sobre o mesmo snapshot imutável.

Comparadas as opções, reaproveitar a representação validada tem risco menor que agrupar gravações de trace ou reduzir etapas de decisão. As gravações continuam importantes para `flush`, progresso visível e recuperação; não foi demonstrado que removê-las preservaria essas garantias. Esta rodada otimiza apenas preparação local, mantendo seleção, autoridade, conteúdo dos prompts e evidências observáveis.

## Implementação

O resultado imutável de `disclose_tools` guarda o índice e os detalhes que acabaram de passar pelos limites de apresentação. Métricas e montagem do prompt usam esses mesmos textos quando o limite de contexto coincide.

Os três campos internos são excluídos do construtor, da representação textual e da comparação do dataclass. A cópia por `dataclasses.replace` restaura esses campos aos valores vazios, impedindo transportar cartões antigos para uma seleção substituída. Resultados construídos diretamente seguem a renderização e validação normais. Um orçamento diferente também refaz a validação e pode rejeitar o catálogo.

Não existe cache global de prompts ou catálogo, nem opção nova na interface. As strings têm o tempo de vida do resultado da descoberta; os snapshots de elegibilidade e as validações de execução continuam nos responsáveis existentes.

Código: [tool_disclosure.py](../../../src/llm_agent/agent/planning/tool_disclosure.py). Casos de regressão: [test_tool_guidance_reuse.py](../../../tests/unit/planning/test_tool_guidance_reuse.py).

## Medição reproduzível

```powershell
.venv\Scripts\python.exe scripts/benchmark_tool_guidance.py --compare --samples 5 --repetitions 100 --output reports/tool-guidance-comparison-nova.json
```

O benchmark usa o fluxo real de descoberta e de preparação do prompt com um seletor substituído por uma função local. A referência desabilita somente o reaproveitamento dos textos, recriando a renderização repetida no mesmo código. Cinco amostras alternam a ordem das duas variantes.

| Cenário, 100 ciclos por amostra | Referência | Reaproveitamento |
|---|---:|---:|
| Quatro ferramentas: renderizações de índice | 200 | 100 |
| Quatro ferramentas: renderizações detalhadas | 300 | 100 |
| Quatro ferramentas: mediana local | 90,533 ms | 41,318 ms |
| Oito ferramentas: renderizações de índice | 300 | 100 |
| Oito ferramentas: renderizações detalhadas | 300 | 100 |
| Oito ferramentas: mediana local | 153,052 ms | 78,083 ms |
| Oito ferramentas: pedidos ao seletor simulado | 100 | 100 |

Os hashes do texto entregue ao modelo e dos eventos de diagnóstico são idênticos entre variantes e em todas as amostras. O ganho confirmado é a remoção de dois terços das renderizações detalhadas repetidas e a redução do tempo nessa etapa local. Não demonstra a mesma redução no tempo total de uma tarefa nem em latência de inferência.

Artefatos: [antes da implementação](../../../reports/tool-guidance-before-20261101.json), [depois](../../../reports/tool-guidance-after-20261101.json) e [comparação repetida](../../../reports/tool-guidance-comparison-20261101.json).

## Regressões e conformidade

Os testes verificam igualdade do conteúdo, rejeição de catálogos grandes já na descoberta, nova validação com limite menor, reconstrução da seleção sem levar cartões anteriores e validação de resultados construídos manualmente.

A execução ampla também encontrou duas expectativas antigas de GBNF numa fixture do fluxo reativo que não declarava capabilities. Uma reprodução isolada no pacote anterior `8be32aba36cd-20261101T200621Z-d50b34f3` confirmou que esse gateway já recebia uma requisição sem GBNF. A fixture passou a declarar suporte explicitamente; as asserções de gramática foram mantidas. Dois testes adicionais garantem que suporte ausente ou somente JSON por prompt continua sem gramática. Nenhuma permissão ou capability foi ampliada no produto.

Ruff, complexidade até 10 por função e Mypy dos dois módulos/script passaram para Windows e Linux como plataformas estáticas. A arquitetura CURRENT passou com 979 módulos e 3.451 arestas. A [matriz de 44 tarefas simuladas](../../../reports/weak-model-v2-fixture-20261101-guidance.json) passou com fontes inalteradas, zero falsos sucessos e zero efeitos não autorizados. Seu gate de qualidade de modelo continua inconclusivo porque não executa modelo real.

A repetição da suíte ampla após corrigir a declaração da fixture passou com **1.429 testes aprovados e um ignorado**. A equivalência dos hashes foi conferida em todas as vinte linhas do benchmark repetido, e as fontes atuais correspondem ao fingerprint do relatório da matriz.

## Instalação verificada

O candidato `555947b289a9-20261101T202608Z-7afd5cdb`, versão `0.2.0rc1`, foi instalado offline e validado. A versão anterior `8be32aba36cd-20261101T200621Z-d50b34f3` foi preservada.

A conferência independente confirmou pacote íntegro e `update_available=false`. O smoke pelo Python isolado do pacote instalado confirmou uma renderização de índice e uma de detalhes no fluxo completo, seguida de rejeição ao reduzir o orçamento para um catálogo que deixou de caber. Nenhum modelo foi chamado.

SHA-256 do wheel: `49276db835b94fa2d2133e0b85fcd1ed50078062027ddf9487de116076391719`. Reabra o Workbench para carregar a atualização.
