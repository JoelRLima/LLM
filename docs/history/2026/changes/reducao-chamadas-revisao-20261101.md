# Redução de chamadas sem remover a revisão de conclusão

Os artefatos locais citados neste documento não são versionados nem publicados com o repositório. Seus caminhos são referências da investigação original e não comprovam a validação do candidato atual.

Rodada de 1º de novembro de 2026. O objetivo foi reduzir etapas de modelo, mantendo o fluxo de autoridade, validação e conclusão. Não houve inferência ou acesso a endpoint real.

## Escolha pelo alcance

A instrumentação da suíte padrão encontrou 42 descobertas de ferramentas: 40 com 18 ferramentas elegíveis e duas com nove. Portanto, ampliar o atalho de catálogos pequenos teria pouco alcance nessa matriz.

O ponto de maior ganho demonstrável foi a fronteira de raciocínio após o plano. Antes de revisar se os resultados bastavam, o harness repetia a seleção de ferramentas, mesmo quando o próximo resultado era somente uma decisão `complete` ou `blocked`.

Foi escolhido reaproveitar a **seleção de ferramentas**, mantendo a revisão pelo modelo e todas as verificações posteriores. Não foram removidos contratos, especificações, roteamento de persona, revisão de obrigações ou validação de efeitos.

## Comportamento implementado

O reaproveitamento só acontece quando:

- Existe uma seleção anterior válida e não vazia, para o mesmo objetivo, tentativa de execução e nó da tarefa.
- Capacidades e snapshots de autoridade permanecem iguais.
- O contexto, o runtime identity, os descritores, o conjunto atualmente apresentado de ferramentas e o orçamento de contexto continuam compatíveis com os snapshots canônicos.
- O catálogo é maior que o limiar que já dispensa descoberta. Caminhos pequenos, sem correlação ou sem seleção válida seguem o fluxo anterior.

Observações, progresso, obrigações e contexto da tarefa são preparados novamente. A fronteira de execução obrigatória continua sendo reinjetada pelo dono do contexto em cada chamada. O resultado anterior da tarefa ou do modelo não é reaproveitado.

Se a revisão retornar `execute` com uma ferramenta fora da seleção anterior, o candidato é descartado antes de aplicar plano, obrigações ou efeitos. O harness faz uma descoberta normal sobre o catálogo atual e pede uma nova decisão, com detalhes atualizados. Isso ocorre no máximo uma vez por invocação dessa revisão; uma segunda proposta fora da seleção vai para a validação normal e pode ser recusada. Ferramentas em ramos condicionais também são conferidas.

Uma decisão `complete` continua passando pelo dono canônico de conclusão, incluindo revisão de obrigações, requisitos, evidências, validação e efeitos pendentes. Visibilidade não concede permissão de execução. O orçamento real contabiliza cada chamada adicional e continua podendo interromper o fluxo.

O caso comum economiza uma chamada de descoberta. Quando uma ferramenta nova é necessária, a revisão antecipada pode custar uma chamada a mais que o fluxo antigo. É uma troca deliberada, com retorno limitado ao fluxo normal, não uma garantia de economia em toda tarefa.

Implementação: [boundary_discovery.py](../../../../src/llm_agent/agent/planning/boundary_discovery.py) e [plan_builder.py](../../../../src/llm_agent/agent/planning/plan_builder.py). Casos de fronteira: [test_boundary_discovery.py](../../../../tests/unit/planning/test_boundary_discovery.py).

## Comparação pareada reproduzível

```powershell
.venv\Scripts\python.exe scripts/benchmark_boundary_discovery.py --output-dir reports/boundary-discovery-nova
```

O script executa 44 tarefas simuladas por braço, 22 em cada perfil. Na referência, força explicitamente a descoberta normal em toda revisão, substituindo apenas a função que admite o reaproveitamento. Esse override fica registrado em `runtime_test_override`; os arquivos de fonte são os mesmos entre braços. O candidato usa o comportamento de produção. Todos os 88 resultados são preservados.

| Medida em 22 tarefas por perfil e braço | Padrão: referência | Padrão: candidato | Compacto: referência | Compacto: candidato |
|---|---:|---:|---:|---:|
| Chamadas simuladas de modelo | 189 | 171 | 190 | 173 |
| Chamadas de descoberta | 42 | 24 | 43 | 26 |
| Revisões da fronteira de raciocínio | 18 | 18 | 18 | 18 |
| Tokens contabilizados pelo harness | 674.004 | 616.761 | 680.717 | 626.045 |
| Tarefas aprovadas | 22 | 22 | 22 | 22 |

No perfil padrão, a redução foi de **18 chamadas, ou 9,5%**, com 8,5% menos tokens contabilizados. No compacto, foram 17 chamadas, ou 8,9%. São contagens da suíte simulada; os tokens usam a contabilização do harness, não cobrança real do provedor.

Os resultados de tarefa, códigos de falha, arquivos alterados, falsos sucessos e efeitos não autorizados coincidiram entre braços. Todas as tarefas passaram nos oráculos. As 18 revisões de raciocínio por perfil foram mantidas. Os fingerprints de fonte coincidem e as fontes não mudaram durante as execuções.

Artefatos: comparação (artefato local: `reports/boundary-discovery-20261101/comparison.json`), referência completa (artefato local: `reports/boundary-discovery-20261101/fresh-selection-reference.json`) e candidato completo (artefato local: `reports/boundary-discovery-20261101/same-task-selection.json`).

## Verificação e limites

- Regressão ampla de planejamento, LLM e rota compacta: **1.448 testes aprovados e um ignorado**.
- Os testes novos verificam mudança de tarefa, tentativa, objetivo, capabilities, autoridade, contexto, catálogo e orçamento; preservação de dados novos; pedidos de ferramenta nova em passos e ramos condicionais; invalidez de binding; limite de atualização; ausência de autorização implícita de escrita; e bloqueio pelo orçamento real de chamadas.
- Ruff, complexidade até 10 por função e Mypy dos módulos e script passaram para Windows e Linux como plataformas estáticas. Isso não equivale a execução em Linux.
- Arquitetura CURRENT aprovada: 980 módulos e 3.460 arestas.

Não foi medida latência de inferência, custo faturado ou acurácia de um modelo real. A seleção anterior pode influenciar a estratégia do modelo, ainda que o índice completo permaneça disponível e exista atualização para ferramenta nova. A comparação confirma correção nos casos cobertos e economia de chamadas da fixture, não ausência universal de regressões semânticas. `standard` continua padrão e `compact` permanece opt-in; não foi adicionado modo de uso novo.

## Instalação verificada

Instalada a versão `eca9119645f6-20261101T205106Z-1ff920d7`, preservando `555947b289a9-20261101T202608Z-7afd5cdb` como anterior. O status confirmou `update_available=false`, o fingerprint do pacote instalado coincide com os metadados e o fingerprint da campanha coincide com as fontes atuais.

Um processo isolado (`python -I`) do ambiente instalado verificou a conclusão com reaproveitamento da seleção e o caminho de ferramenta nova com descoberta e decisão renovadas. Nenhuma ferramenta foi executada e nenhum modelo real foi consultado nesse teste. SHA-256 do wheel: `9771670de6790e7a7948ecc36a3b1cffdfcfdf11993ade5b654213cc16e78c3c`.
