# Isolamento e limite do cache de descoberta

## Problema e prioridade

O cache de seleção de ferramentas usava somente `(snapshot_id, planner_kind, objective)` como chave e crescia sem limite. Os nomes guardados eram revalidados contra o catálogo atual, mas a seleção podia ter vindo de outra tarefa ou autoridade. Uma atualização forçada que falhasse também deixava a seleção anterior disponível para uma chamada seguinte. Isso podia fornecer ao modelo detalhes de ferramentas escolhidas em condições antigas; os controles de execução continuavam presentes.

Priorizei corrigir a validade dessa otimização antes de ampliar seu uso. A alternativa de desativar todo reaproveitamento eliminaria também os acertos válidos. Um prazo de expiração sozinho não detectaria troca imediata de tarefa ou autoridade.

## Mudança

- Reutilização exige a mesma correlação de tentativa/tarefa, objetivo, capacidades, autoridades, contexto canônico completo, apresentação e limite de contexto. A ausência de correlação canônica impede o acerto no cache.
- O escopo é compartilhado com a otimização de revisão da fronteira de raciocínio, incluindo a autoridade do contexto mesmo quando seu ID permanece igual.
- O cache mantém no máximo oito seleções e remove a menos recentemente usada. Não guarda resultados de execução de ferramentas.
- Uma atualização forçada invalida a entrada anterior antes da consulta. Uma resposta inválida ou uma exceção não permite ressuscitar a seleção antiga.
- Uma seleção só entra no cache depois que sua apresentação passa pela validação de orçamento. O caminho de acerto continua validando nomes e renderização; nenhuma permissão de execução é concedida pelo cache.

## Reprodução e resultados

Comparação em processos Python isolados, com respostas sintéticas, contra o pacote anterior `eca9119645f6-20261101T205106Z-1ff920d7`:

| Caso | Pacote anterior | Fonte corrigida |
| --- | --- | --- |
| Mesma tarefa e contexto | 1 consulta; seleção reutilizada | 1 consulta; seleção reutilizada |
| Nova tarefa, mesmo objetivo e catálogo | reutilizou `tool_a` | consultou novamente e selecionou `tool_b` |
| Atualização inválida seguida de nova consulta | recuperou `tool_a` antiga | consultou novamente e selecionou `tool_b` |
| 100 objetivos distintos | 100 entradas retidas | 8 entradas retidas |

Os casos de invalidação fazem uma consulta adicional deliberada: o ganho é corrigir a seleção antiga, não diminuir chamadas nessas situações. O reaproveitamento válido conserva a economia anterior.

Comando reproduzível antes de remover o pacote de referência:

```powershell
.venv/Scripts/python.exe scripts/benchmark_disclosure_cache.py --reference-python .workbench-managed/versions/eca9119645f6-20261101T205106Z-1ff920d7/runtime/Scripts/python.exe --output reports/disclosure-cache-reproduction-20261101.json
```

[Reprodução comparativa](../../../reports/disclosure-cache-reproduction-20261101.json). O script verifica especificamente o defeito do pacote de referência; não é uma comparação genérica entre quaisquer versões.

## Validação

- Planejamento, LLM e rota compacta: **1.466 testes aprovados, um ignorado**.
- Os testes cobrem troca de tentativa/tarefa, autoridades, capacidades, objetivo, catálogo, descrição, schema e orçamento; descarte de entradas legadas; expulsão por uso; atualização inválida e falha de apresentação.
- [Campanha de 44 tarefas simuladas](../../../reports/disclosure-cache-20261101.json): todas aprovadas, com os mesmos resultados, falhas, arquivos alterados e efeitos não autorizados da campanha anterior. Chamadas simuladas preservadas: 171 no perfil padrão e 173 no compacto.
- Ruff e Mypy passaram nos módulos alterados. A verificação estática de tipos também passou com plataforma Linux; não houve execução em Linux.
- Arquitetura CURRENT aprovada: 981 módulos e 3.464 arestas, sem alterar a política ou o baseline nesta rodada.

Não houve inferência real, execução de ferramenta nessa reprodução ou medição de qualidade de modelos reais. O limite de oito entradas limita a quantidade retida, não constitui uma medição de bytes de memória ou de desempenho.

## Instalação

Instalada `88423c595ace-20261101T232117Z-9b62c2dc`, mantendo `eca9119645f6-20261101T205106Z-1ff920d7` como anterior. Status: `update_available=false`. O fingerprint das fontes coincide com a campanha e o fingerprint do pacote instalado coincide com seus metadados.

O mesmo fixture foi repetido no Python isolado (`-I`) do pacote instalado e coincidiu integralmente com os resultados da fonte corrigida: acerto válido com uma consulta, redescoberta nas duas invalidações e oito entradas após 100 objetivos. SHA-256 do wheel: `98656d48d3816d2d125319181794f02a989d94fd40a5549f03834a6aa11d220b`.
