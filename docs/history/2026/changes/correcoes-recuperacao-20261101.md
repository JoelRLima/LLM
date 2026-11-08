# Recuperação limitada sem perder os vínculos do plano

Os artefatos locais citados neste documento não são versionados nem publicados com o repositório. Seus caminhos são referências da investigação original e não comprovam a validação do candidato atual.

Rodada de 1º de novembro de 2026. Toda a validação usou inspeção de código, respostas simuladas e testes locais; nenhum modelo ou endpoint foi chamado.

## Falhas confirmadas e correções

### Reparo de argumentos com requisitos e obrigações

O fluxo tipado de reparo reconstrói o passo substituto com o ID e os vínculos originais (`requirement_ids` e `obligation_ids`). Porém `accepts_constrained_repair` rejeitava qualquer candidato que contivesse esses campos. Assim, um reparo limitado ao argumento inválido era recusado mesmo preservando os vínculos corretamente.

A reprodução inicial retornou `False` para um reparo de `file_path` que mantinha os dois vínculos. A função agora aceita as associações canônicas somente quando sua presença e seus valores permanecem iguais aos do passo original. Associações malformadas, removidas, acrescentadas ou substituídas continuam rejeitadas. Os validadores de plano e autoridade seguem responsáveis pela admissão final.

O teste do fluxo tipado verifica que o candidato segue para a admissão com a mesma ferramenta, ID e associações, e somente o campo autorizado corrigido. Usa o orçamento real de recuperação: consome uma tentativa de `VALIDATION_REPAIRS`, nenhuma de `LLM_REPLANS`, e impede uma segunda tentativa. O teste substitui a resposta do replanejador e a admissão posterior por funções locais; não é inferência nem validação de qualidade de um modelo.

Código: [validation_repair_contracts.py](../../../../src/llm_agent/agent/planning/validation_repair_contracts.py). Regressão: [test_validation_repair_associations.py](../../../../tests/unit/planning/test_validation_repair_associations.py).

### Erro de gramática confundido com ausência de suporte

Antes, qualquer HTTP 400 cujo corpo contivesse a palavra `grammar` era interpretado como ausência de suporte. A reprodução com `invalid grammar syntax: missing root rule` retornou `True`, permitindo remover a restrição e marcar a gramática como não suportada.

O novo [grammar_errors.py](../../../../src/llm_agent/agent/llm/grammar_errors.py) reconhece mensagens explícitas de falta de suporte, parâmetro desconhecido ou GBNF desativado. Erros de sintaxe, regra desconhecida, compilação, contexto, schema ou outro parâmetro não provocam degradação. Quando o corpo é um objeto JSON, lê a mensagem de erro em vez de procurar palavras em campos que podem conter o pedido ecoado. Falhas de autenticação, limite de requisições e servidor também não desativam a gramática.

É um reconhecedor conservador, não uma prova universal de todas as mensagens de todos os servidores. Uma mensagem não reconhecida conserva a falha e a restrição. O fallback reconhecido continua limitado ao comportamento e orçamento existentes.

### Cache deve refletir o formato efetivamente enviado

Uma gramática desejada pelo chamador não significa que a requisição contém GBNF: o perfil pode ter escolhido JSON Schema ou não declarar suporte a gramática. O resolvedor marcava suporte como confirmado mesmo nesses caminhos.

Agora o cache e o fallback só são atualizados quando a requisição contém uma estrutura GBNF com gramática não vazia. Uma resposta JSON comum ou nativa não comprova suporte a GBNF. Os testes cobrem sucesso e falha sem saída estruturada, com JSON Schema e com um pedido GBNF sem gramática.

Código: [structured_output.py](../../../../src/llm_agent/agent/llm/structured_output.py). Regressão: [test_grammar_capability_errors.py](../../../../tests/unit/llm/test_grammar_capability_errors.py).

## Validação

- LLM, planejamento, vínculos, autoridade, reparo e modo compacto: 543 testes aprovados.
- Campanha completa: 44 execuções simuladas aprovadas, fontes inalteradas, zero falsos sucessos e zero efeitos não autorizados. Relatório (artefato local: `reports/weak-model-v2-fixture-20261101-recovery.json`).
- Ruff e complexidade até 10 por função: aprovados. Mypy dos três módulos alterados passou nas plataformas estáticas Windows e Linux; não equivale a execução em Linux.

Os resultados confirmam o comportamento do código nos casos cobertos. O gate de qualidade de modelo permanece inconclusivo, com `model_used=false`. Não houve promoção do modo compacto, ajuste especulativo de thresholds ou aumento de tentativas.

## Comparação e instalação

A reprodução direta contra o pacote anterior `85e295b69d89-20261101T191634Z-c65f3242` confirmou as três diferenças: reparo associado `False` → `True`, erro de sintaxe classificado como falta de suporte `True` → `False`, atualização indevida do cache sem GBNF `[True]` → `[]`.

Após separar os validadores de associações e bindings, a verificação focada passou em 71 testes. A arquitetura CURRENT passou com 978 módulos e 3.447 arestas.

O pacote `e50b653e3a1a-20261101T193651Z-c6356afc`, versão `0.2.0rc1`, foi instalado offline com checagens de dependências, importação isolada e fingerprints. A versão anterior foi preservada. A verificação independente confirmou pacote íntegro, `update_available=false`, fontes correspondentes ao relatório da campanha e reprodução das três correções no pacote instalado. A tentativa de alterar associações continuou bloqueada no smoke instalado.

SHA-256 do wheel: `8adc2e28f595660a095360c570e1f1a568927f5b5408ae8d912df362c46cc77b`. Reabra o Workbench para carregar a atualização. A rodada não alterou a interface visual.
