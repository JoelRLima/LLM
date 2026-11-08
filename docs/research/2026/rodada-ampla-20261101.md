# Rodada ampla: resposta, evidência, recuperação e integração

## Prioridade e cobertura

Foram revisados entrada e cancelamento, planejamento, seleção de ferramentas, progresso, fronteira de resposta do provedor, contabilidade de chamadas e apresentação do resultado pela interface. As mudanças se concentram nos defeitos reproduzidos. Não foram adicionados novos modos, configurações ou decisões para o usuário.

| Prioridade | Problema confirmado | Correção |
| --- | --- | --- |
| P0 | Stream encerrado por EOF era tratado como conclusão normal; `finish_reason=length/content_filter` era ignorado | Exigir marcador terminal e tratar truncamento/filtro como falha |
| P0 | Exceções de transporte ou parsing perdiam conteúdo parcial e uso de tokens já recebido | Preservar esses fatos na exceção consumida pelo serviço canônico de chamadas |
| P1 | Metadados de obrigações com quebras de linha podiam formar falsas linhas no contexto de progresso | Serializar cada obrigação como registro JSON e indicar que seus textos são dados |
| P1 | Reparo da descoberta dizia apenas que a seleção era inválida | Informar as posições dos nomes ausentes do catálogo, sem reinserir texto arbitrário do modelo |
| P1 | Suíte ampliada tinha 50 falhas já presentes no pacote anterior | Atualizar fixtures para contratos e portas atuais, mantendo asserções |

## Decisões de implementação

Um stream pode concluir com `[DONE]` ou com `finish_reason` não nulo. Isso preserva servidores compatíveis que enviam apenas um desses marcadores. EOF sem nenhum deles produz erro. `length` e `content_filter` produzem erro mesmo com `[DONE]`. O adaptador continua recebendo uso de tokens após o motivo terminal para não descartar esse dado. O socket é fechado inclusive quando o consumidor interrompe no evento de erro.

O texto parcial pode continuar visível para diagnóstico, mas não se converte em retorno bem-sucedido do serviço de chamadas. Uso observado é preservado e a chamada é finalizada uma única vez no ledger. Isso não cria uso observado quando o servidor não o enviou.

Na projeção de progresso, status e evidências continuam vindo dos mesmos proprietários canônicos. JSON escapa quebras de linha e controles contidos em descrições e identificadores, sem apagar seu conteúdo. Isso impede a falsificação de novas linhas nesse formato; não demonstra resistência universal a prompt injection.

O reparo de ferramentas continua limitado pelo orçamento existente. O feedback identifica somente posições numéricas, como `[1, 3]`, e manda consultar novamente o catálogo atual. Não insere os nomes rejeitados como instruções, não corrige a escolha automaticamente e não amplia autoridade.

Essas escolhas são consistentes com a distinção de término/truncamento da [referência de streaming da OpenAI](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events) e com a orientação de fornecer erros específicos e acionáveis para agentes em [Writing effective tools — Anthropic](https://www.anthropic.com/engineering/writing-tools-for-agents). São fundamentos de projeto; as correções concretas foram verificadas localmente.

## Reprodução contra o pacote anterior

Processos isolados usaram o pacote `88423c595ace-20261101T232117Z-9b62c2dc` e a fonte corrigida com os mesmos dados sintéticos. A resposta normal coincide. No anterior, EOF e truncamento retornavam sucesso; na fonte corrigida, retornam falha preservando `partial` e o uso observado de sete tokens. Na falha de transporte, o anterior perdia texto e uso; a fonte os mantém. A descrição multilinha deixa de criar uma linha falsa de status.

```powershell
.venv/Scripts/python.exe scripts/benchmark_runtime_boundaries.py --reference-python .workbench-managed/versions/88423c595ace-20261101T232117Z-9b62c2dc/runtime/Scripts/python.exe --output reports/runtime-boundaries-reproduction-20261101.json
```

[Resultados brutos da reprodução](../../../reports/runtime-boundaries-reproduction-20261101.json). O script verifica especificamente os defeitos desse pacote de referência, não qualquer par de versões.

## Recuperação da suíte ampliada

A primeira execução passou em 2.278 testes, ignorou 21 e falhou em 50. Os mesmos 50 testes também falharam usando os imports do pacote anterior, antes de alterar os fixtures. As causas encontradas foram:

- `JourneyGateway` reconhecia texto antigo do prompt inicial e emitia planos sem `action`. Agora atende ao contrato `INITIAL_PLAN` e gera `action=use_tools` quando há plano. Os oráculos de resultado, arquivos e efeitos foram mantidos.
- Fixtures de cancelamento não expunham o runtime atual, e o spy de memória mirava uma função removida do adaptador CLI. Agora usam o runtime e a porta do serviço compartilhado; as verificações de ausência de efeitos permanecem.
- Um teste de continuação exigia gramática de um gateway que só declarava JSON por prompt. O suporte GBNF é declarado apenas nesse teste simulado; nenhuma capacidade de backend real foi alterada.

## Validação final, campanha e limites

A suíte ampliada terminou com **2.328 testes aprovados e 21 ignorados**. [Resultado bruto JUnit](../../../reports/rodada-ampla-tests-20261101.xml). Houve um aviso de descarte de variável Tk fora da thread principal durante um teste de interface; não foi uma falha de asserção, mas permanece registrado como limitação da execução, sem atribuir uma causa não comprovada.

[44 tarefas simuladas](../../../reports/rodada-ampla-final-20261101.json) passaram, com resultados, códigos de falha, arquivos alterados, falsos sucessos e efeitos não autorizados iguais à rodada anterior. As contagens de chamadas permanecem 171 no padrão e 173 no compacto. O [primeiro artefato desta rodada](../../../reports/rodada-ampla-20261101.json) também foi preservado; o final registra a fonte após trocar a atribuição dinâmica de atributos de erro por `setattr`, sem mudança comportamental.

Os tokens contabilizados aumentaram de 616.761 para 617.504 no padrão e de 626.045 para 626.788 no compacto: 743 por perfil, associados à projeção explícita de metadados. Não é cobrança real. A mudança privilegia clareza da fronteira de dados; não houve alegação de economia adicional de quota.

Ruff e Mypy passaram nos módulos alterados e no script. A análise de tipos também passou com plataforma Linux, sem execução em Linux. Arquitetura CURRENT aprovada: 981 módulos e 3.464 arestas. A complexidade das funções modificadas permanece até dez; a função preexistente `build_payload`, sem alteração nesta rodada, tem onze.

Não houve consulta a modelo real, benchmark de latência de inferência ou medição de acurácia de modelos menores. Streams e respostas de modelo foram simulados. A cobertura ampliada é uma seleção de áreas do repositório, não toda a suíte existente.

## Instalação e conferência independente

Instalada `b749ff38cc0a-20261101T235324Z-37b40a6b`, preservando `88423c595ace-20261101T232117Z-9b62c2dc` como anterior. O status confirmou `update_available=false`; o fingerprint do pacote coincide com seus metadados e as fontes coincidem com a campanha final.

Após o ajuste final de tipagem, 31 testes focados de fronteira, reparo e contabilidade passaram. A reprodução de stream e progresso também foi repetida no Python isolado (`-I`) da instalação e coincidiu integralmente com o resultado da fonte corrigida. SHA-256 do wheel: `8cc6150703f18e03caa87c38611a8d9ee4719608869578e9c909ca46bf6fd48c`.
