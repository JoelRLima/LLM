# Decisões inequívocas antes de executar ferramentas

Os artefatos locais citados neste documento não são versionados nem publicados com o repositório. Seus caminhos são referências da investigação original e não comprovam a validação do candidato atual.

Rodada complementar de 1º de novembro de 2026, voltada à correção do protocolo e à transparência. Não altera o modelo padrão, a elegibilidade de ferramentas, a autoridade ou os critérios de conclusão.

## Problemas reproduzidos

A comparação direta com o pacote anterior `e1e962d5ddee-20261101T184714Z-858d19ec` confirmou cinco decisões indevidamente admitidas. A mesma reprodução contra o código novo rejeitou todas:

| Entrada | Pacote anterior | Código novo |
|---|---|---|
| Objeto externo truncado contendo uma chamada interna completa | Admite o objeto interno | Rejeita |
| Duas decisões JSON consecutivas | Escolhe a última | Rejeita |
| Chave `action` repetida com valores diferentes | Usa o último valor | Rejeita |
| Argumento numérico `NaN` | Admite | Rejeita |
| Chamada JSON completa com `finish_reason=length` | Admite | Rejeita |

Essa é uma reprodução determinística do parser e da admissão, sem inferência. Não mede acurácia de um modelo.

## Mudanças

O novo [model_json.py](../../../../src/llm_agent/agent/llm/model_json.py) decodifica uma única raiz, sem procurar uma decisão alternativa dentro de um objeto incompleto. Rejeita chaves repetidas também em objetos aninhados e equivalentes por escape Unicode; rejeita `NaN`, infinitos e números que transbordam para infinito. Profundidade excessiva vira erro estruturado. Continua aceitando um prefixo textual simples e um bloco Markdown completo com JSON. Não tenta consertar intenção escolhendo um fragmento posterior.

Em [structured_output.py](../../../../src/llm_agent/agent/llm/structured_output.py), `parse_model_response` verifica os metadados de término antes de ler uma decisão. Saídas marcadas como `length` ou `content_filter` não são admitidas, mesmo que o conteúdo aparente ser JSON completo. O reparo de truncamento reutiliza a única tentativa já existente, sujeita à autorização e ao orçamento canônicos. Uma interrupção por filtro não dispara outra tentativa.

Erros do resolvedor distinguem `MODEL_OUTPUT_INCOMPLETE`, `MODEL_OUTPUT_FILTERED` e `MODEL_DECISION_INVALID`. Após duas respostas inválidas, o resultado preserva a última em `raw_response` e a primeira em `initial_raw_response`. São dados de diagnóstico; não se tornam instrução ou autorização. Isso não implica que toda interface já apresente esses códigos ao usuário.

Os caminhos de proposta de código, tanto o formato histórico de changeset quanto a decisão CHANGE/NO_CHANGE/NEEDS_INPUT, usam a mesma verificação de término antes de aprovar uma proposta. O verificador independente também rejeita um parecer interrompido e devolve evidência insuficiente. Testes verificam ausência de aprovação e de escrita após propostas interrompidas.

## Fundamento e limites

A [documentação oficial da OpenAI sobre saídas estruturadas](https://developers.openai.com/api/docs/guides/structured-outputs) apresenta tratamento explícito de respostas incompletas e interrupções por limite de saída ou filtro. A [documentação do parser JSON do Python](https://docs.python.org/3/library/json.html#repeated-names-within-an-object) explica que o comportamento padrão conserva o último valor de uma chave repetida e aceita extensões numéricas não finitas. Rejeitar esses casos no ingresso de decisões é uma escolha do harness para evitar ambiguidade operacional.

Essa proteção não confirma a veracidade dos argumentos, a escolha correta da ferramenta ou o resultado da tarefa. As validações de domínio, aprovação, autoridade, evidência e efeitos continuam necessárias. Não foram alterados thresholds de compactação ou seleção de ferramentas sem baseline com modelo real.

## Verificação

- Suíte LLM: 306 testes aprovados. Após compartilhar a verificação de término com a engenharia, o parser e os contratos foram reavaliados junto das suítes de código.
- Planejamento, autoridade, conclusão, rota compacta e gates selecionados: 181 testes aprovados.
- Engenharia e novos casos do parser: 137 aprovados, quatro ignorados; inclui propostas e pareceres interrompidos. Os conjuntos de testes se sobrepõem.
- Suíte de avaliação: 180 testes aprovados. A campanha completa passou em 44 execuções simuladas, com fontes inalteradas, zero falsos sucessos e zero efeitos não autorizados: relatório final (artefato local: `reports/weak-model-v2-fixture-20261101-parser-final.json`). O gate de qualidade de modelo segue inconclusivo porque nenhum modelo foi executado.
- Ruff e complexidade até 10 nas funções alteradas: aprovados. Mypy dos quatro módulos alterados passou nas plataformas estáticas Windows e Linux; isso não é execução em Linux.

O modelo real permanece sem medição nesta rodada. O perfil `compact` continua opt-in.

## Instalação local validada

Arquitetura CURRENT aprovada: 977 módulos, 3.445 arestas. O pacote `85e295b69d89-20261101T191634Z-c65f3242`, versão `0.2.0rc1`, foi instalado pelo gerenciador offline, com `pip check`, importação isolada e fingerprints conferidos. A versão anterior `e1e962d5ddee-20261101T184714Z-858d19ec` foi preservada. A verificação independente confirmou `update_available=false`, integridade do pacote e correspondência entre as fontes atuais e o relatório final da campanha.

Os cinco casos da reprodução também foram rejeitados pelo pacote instalado, enquanto uma chamada normal continuou admitida. A verificação de término de propostas foi exercitada no mesmo pacote. SHA-256 do wheel: `8d224d5fc2c5153f227bca787ad0d7bc437179352fb6f7d5119d13ad38ac6b72`.

Reabra o Workbench para carregar a atualização. Não houve alteração visual nesta rodada nem nova verificação da janela nativa.
