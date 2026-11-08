# Gargalos do agente: diagnóstico, escolha e implementação

Análise de 1º de novembro de 2026. Nenhum endpoint ou modelo real foi chamado. A investigação combinou inspeção do caminho de produção, perfil local de cinco tarefas simuladas, relatórios da matriz de 22 tarefas e benchmark do transporte HTTP substituído por funções locais.

## Prioridades encontradas

| Prioridade | Gargalo e evidência | Opção considerada | Decisão |
|---|---|---|---|
| 1 — potencial de latência | A matriz padrão registra 189 chamadas simuladas em 22 tarefas: 42 de descoberta de ferramentas, 22 de contrato, 22 de especificação e 22 de roteamento. A descoberta representa 22,2% das chamadas. | Reduzir decisões intermediárias ou usar shortlist determinística. | Maior potencial semântico, mas alterar essas etapas pode prejudicar escolha de ferramenta e autoridade. As contagens não medem tempo de inferência; não remover verificações sem evidência de qualidade. |
| 2 — repetição eliminável confirmada | A mesma requisição é medida no ajuste de contexto e novamente na admissão. No caminho de contagem exata são duas consultas ao contador; no fallback de texto, duas consultas ao tokenizer. | Reaproveitar brevemente a medição já obtida para o mesmo objeto e payload. | **Implementado:** melhor relação entre ganho demonstrável, alcance e risco nesta rodada. Não muda planejamento, ferramentas ou critérios de sucesso. |
| 3 — custo local de persistência | No perfil de cinco tarefas, publicação de metadados e índice de traces aparece entre os maiores custos. O perfil registrou 168 gravações atômicas e 84 atualizações de índice. Uma execução instrumentada separada encontrou 79 gravações de metadados e 79 de índice, sem gravações idênticas. | Agrupar publicações ou alterar o armazenamento do índice. | Adiado: envolve durabilidade, progresso visível, `flush` e recuperação após falha. Pular gravações idênticas não resolveria o caso medido. |
| 4 — montagem de contexto | Memória, pistas de arquivos e orientação do projeto são construídas por etapa; parte opcional pode ser descartada depois do dimensionamento. | Construção sob demanda e cache de fontes. | Candidato posterior. Invalidação precisa acompanhar alterações no workspace e nos fatos do runtime; não foi quantificado como principal custo desta amostra. |

O perfil local levou aproximadamente 4,2 s, com `cProfile` ativo. Seus tempos acumulados incluem espera e podem se sobrepor entre caminhos concorrentes; não são parcelas somáveis de latência. A diferença entre contagens de publicações nas duas execuções decorre do agendamento assíncrono. O perfil bruto de investigação está em `.tmp/agent-bottlenecks-before.prof`; as contagens de chamadas semânticas vêm do [relatório anterior](../../../reports/weak-model-v2-fixture-20261101-recovery.json).

As recomendações oficiais de [otimização de latência da OpenAI](https://developers.openai.com/api/docs/guides/latency-optimization) incluem reduzir requisições e aplicar técnicas convencionais de otimização. A experiência da Anthropic sobre [harnesses para tarefas longas](https://www.anthropic.com/research/harness-design-long-running-apps) também explicita custos adicionais de orquestração, tokens e latência. Essas fontes orientaram a comparação; não demonstram ganhos neste repositório. A escolha aqui depende das reproduções locais acima.

## Solução implementada

O provedor OpenAI-compatible passa a oferecer ao medidor canônico uma transferência de medição entre ajuste de contexto e admissão. Não é cache de resposta do modelo.

- Reutiliza no máximo uma vez, por até um segundo, somente no mesmo objeto de requisição e com fingerprint igual do payload efetivo, endpoint, perfil, capabilities, opções de contador e referência de credencial.
- O armazenamento é volátil, limitado a oito entradas, com referência fraca ao objeto. Não persiste conteúdo de prompts nem credenciais; o fingerprint é um hash.
- Mudanças em mensagens, schema, opções, endpoint, reserva de saída ou identidade configurada impedem reutilização. Uma mudança durante a própria medição também impede guardar o resultado.
- Somente contagens disponíveis do provedor ou do tokenizer de texto são guardadas. Heurística e indisponibilidade continuam sendo recalculadas; um tokenizer de texto permanece aproximado.
- O registro é descartado antes de iniciar `complete` ou `stream`, inclusive quando o transporte falha. Uma geração de despacho impede guardar tardiamente uma medição que atravessou o início de um despacho concorrente.
- Contador explícito fornecido pelo chamador não herda uma medição anterior. Gateways sem essa capacidade seguem o caminho existente.

As verificações de contexto, cancelamento, política, reserva de orçamento e contabilização continuam sendo executadas. A medição exige estabilidade do tokenizer/servidor durante esse curto intervalo, como qualquer dimensionamento antes do envio; não identifica mudanças externas invisíveis de pesos ou template atrás de um alias. Não há reaproveitamento entre objetos apenas por terem textos iguais.

Código: [request_measurement_reuse.py](../../../src/llm_agent/agent/runtime/request_measurement_reuse.py), [request_measurement.py](../../../src/llm_agent/agent/runtime/request_measurement.py), [openai_input_tokens.py](../../../src/llm_agent/agent/llm/providers/openai_input_tokens.py) e [openai_compatible.py](../../../src/llm_agent/agent/llm/providers/openai_compatible.py).

## Comparação reproduzível

```powershell
.venv\Scripts\python.exe scripts/benchmark_request_measurement.py --compare --repetitions 20 --output reports/request-measurement-comparison-nova.json
```

O script bloqueia o caminho HTTP real substituindo `requests.post` e usa um gateway novo por repetição. Desabilitar a transferência no benchmark reproduz o caminho sem reaproveitamento, mantendo o mesmo backend simulado e a mesma admissão.

| Modo, em 20 despachos | Sem reaproveitamento | Com reaproveitamento |
|---|---:|---:|
| Consultas exatas bem-sucedidas | 40 | 20 |
| Consultas ao tokenizer de texto | 40 | 20 |
| Tentativas de endpoint exato indisponível, no modo texto | 20 | 20 |
| Conclusões simuladas em cada modo | 20 | 20 |
| Chamadas de modelo no ledger em cada modo | 20 | 20 |
| Tokens contabilizados no ledger em cada modo | 660 | 660 |

Artefatos: [referência antes da implementação](../../../reports/request-measurement-before-20261101.json), [resultado posterior](../../../reports/request-measurement-after-20261101.json) e [comparação pareada reproduzível](../../../reports/request-measurement-comparison-20261101.json).

O ganho confirmado é a eliminação de 50% das consultas duplicadas ao contador/tokenizer nesse fluxo. Isso não significa agente 50% mais rápido, economia de tokens de inferência ou melhoria de acurácia. O benchmark sem latência de rede apresentou tempos locais semelhantes, aproximadamente 175–179 ms para 20 repetições. Não foi medida a latência de um servidor real.

## Validação

- 447 testes de LLM, limites de contexto, reservas, contabilização e execução de chamadas aprovados.
- 20 testes específicos aprovados, cobrindo expiração, uso único, capacidade limitada, alterações de payload, falhas de transporte, gravação de traces e impossibilidade de ultrapassar o orçamento mesmo com medição reaproveitada. Os conjuntos se sobrepõem.
- Ruff e complexidade até 10 por função aprovados. Mypy dos cinco módulos/script passou para Windows e Linux como plataformas estáticas; não é execução em Linux.
- Arquitetura CURRENT aprovada: 979 módulos, 3.451 arestas.
- Matriz completa: 44 tarefas simuladas aprovadas, sem falsos sucessos ou efeitos não autorizados, com fontes inalteradas. [Relatório desta rodada](../../../reports/weak-model-v2-fixture-20261101-measurement.json).

O perfil padrão continua `standard`, e `compact` permanece opt-in. As melhorias de seleção de ferramentas e persistência ficam identificadas com seus riscos, sem promoção baseada apenas em raciocínio manual ou em tempo de fixture.

## Instalação verificada

Instalado offline o candidato `8be32aba36cd-20261101T200621Z-d50b34f3`, versão `0.2.0rc1`. O gerenciador verificou dependências, importação isolada e fingerprints antes de promover o candidato. A versão anterior `e50b653e3a1a-20261101T193651Z-c6356afc` foi preservada.

A verificação independente confirmou integridade do pacote, fontes correspondentes à matriz final e `update_available=false`. O smoke executado pelo Python do pacote instalado confirmou uma única consulta ao contador por despacho nos dois modos, preservando a distinção exato/aproximado e o ledger de uma conclusão com 33 tokens contabilizados. Todo o HTTP foi simulado.

SHA-256 do wheel: `bac7d474c0b8eff5c2667acae4113e3480bd5a1f6f38269fb76f49068eb1f107`. Reabra o Workbench para carregar a atualização.
