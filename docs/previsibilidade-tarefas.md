# Previsibilidade de tarefas

Este documento define as responsabilidades do fluxo de tarefa e a campanha
determinística que protege interpretação, execução e conclusão.

## Responsabilidades

| Etapa | Responsável existente | Informação que pode produzir |
| --- | --- | --- |
| Interpretar o pedido | `InteractionResolver` e guards | Responder, perguntar, iniciar tarefa ou continuar; candidatos não concedem autoridade. |
| Admitir a definição | `TaskDefinitionCompiler` e repositório | Contract e Spec imutáveis, vinculados por identidade e digest. |
| Materializar contexto | `TaskContextResolver` | Projeção confiável da definição, incluindo assumptions e open_questions. |
| Admitir efeitos e obrigações | `TaskSemantics` e prova positiva | Requisitos duráveis e efeitos admissíveis; o plano não amplia o escopo. |
| Planejar e executar | Gateways, PlanExecutor e ToolExecutor | Passos substituíveis e resultados canônicos correlacionados. |
| Projetar validação atual | `current_validation_evidence` | Fatos de validação por recurso; não decide a autoridade da tarefa nem cria permissões. |
| Decidir conclusão | `review_task_completion` | Revisão de obrigações, efeitos, falhas, cancelamento e validação exigida. |
| Apresentar o resultado | Snapshot e receipt | Projeções da execução; texto do modelo e UI não promovem sucesso. |

## Evidência atual no fluxo de correção

Quando uma tarefa admitida exige validação, uma mutação anterior aprovada não
valida automaticamente as mudanças seguintes. A projeção compartilhada aplica
estas regras:

- Cada recurso tocado conserva o resultado da observação mais recente que o
  modificou ou reverteu. Uma observação de outro recurso não substitui esse fato.
- Uma correção posterior validada pode substituir a falha anterior no mesmo
  recurso. Um reparo parcial deixa os demais recursos pendentes.
- Para comprovar validação, o resultado deve ser canonicamente bem-sucedido
  (`ok: true`, `status: succeeded`), a mutação deve sobreviver sem rollback e o
  artefato correspondente deve declarar `validation_status: passed`.
- Dois artefatos contraditórios dentro da mesma invocação não validam um ao
  outro. A validação de um artefato não cobre outro artefato sem validação.
- Uma mutação sem footprint verificável permanece sem comprovação de escopo.
- Uma tentativa que comprovadamente não alterou nada não fabrica uma nova
  mutação. As obrigações e os efeitos pendentes continuam sujeitos ao revisor
  de conclusão existente.

Snapshots e recibos usam essa mesma projeção. A amostra de apresentação pode
limitar o histórico exibido; o cálculo da validação atual examina o histórico
completo disponível. A informação `ran` descreve execução observada de
validação; o `outcome` pode ser `unverified` após uma mudança posterior.

Quando existe histórico de ferramentas, ele é a fonte da ordem das evidências.
Um `last_result` restaurado separadamente não pode substituir a última mutação
registrada. A compatibilidade que usa somente `last_result` permanece restrita
ao caso em que o histórico está vazio. Os detalhes finais de código, validação
e saída também são calculados sobre o histórico completo, independentemente
da amostra de até 50 observações exibida no relatório.

Essa projeção não substitui a revisão de falhas operacionais, a resolução de
condições ou a validação de obrigações canônicas. Ela também não detecta por si
só mudanças externas ao runtime feitas depois da última observação.

## Identidade dos requisitos

`TaskContextMaterialization.requirement_references` e o campo `requirement_refs`
de `to_dict()` oferecem referências estáveis para requirements, constraints,
invariants, success criteria e critérios/evidências das fases. Cada referência
combina o digest exato da definição com sua origem, índice e identidade da fase.

Recarregar ou selecionar uma fase preserva a identidade da mesma entrada.
Alterar a Spec troca as referências da Spec e preserva as do Contract inalterado.
Entradas textuais iguais em posições diferentes continuam distinguíveis.

Ao selecionar uma fase, o contexto das dependências preserva todos os campos
da fase declarada, incluindo invariantes e requisitos de evidência. Essas
entradas mantêm as mesmas referências encontradas na projeção da Spec completa.

O catálogo não concede efeitos nem declara requisitos satisfeitos. Um recorte
limitado alimenta as decisões de planejamento, e os slots podem declarar
`requirement_ids` e `obligation_ids` para relacionar requisitos, etapas e
observações. Snapshot e recibo publicam a cobertura sem converter a declaração
do modelo em comprovação. Veja [planejamento por resultados](planejamento-por-resultados.md).
O catálogo completo não é duplicado no prompt de cada chamada de modelo.
Checkpoints continuam armazenando apenas `TaskDefinitionRef`, sem cópias dos
corpos nem um segundo estado mutável de requisitos.

Os critérios livres de Contract/Spec ainda não constituem um verificador
genérico de linguagem natural. A evidência normativa continua pertencendo às
obrigações tipadas e aos verificadores já admitidos pelo runtime. A associação
automática de qualquer critério textual a um verificador exigiria um contrato
estruturado adicional e validação própria; comparar textos ou aceitar uma
alegação do modelo não comprova cumprimento.

## Ambiguidade, correção e continuidade

O resolver preserva assumptions e open_questions no contexto confiável, para
que deixem de desaparecer entre a definição e as chamadas seguintes. Esses
campos não autorizam ações. Questões impeditivas usam o resultado existente
`TaskDefinitionNeedsInput`; informações descobríveis no workspace devem ser
tratadas na execução conforme o escopo admitido.

Uma tarefa já admitida conserva seu Contract/Spec imutável. `/continue` retoma
a identidade existente; não recebe outro objetivo. Follow-ups durante uma
execução permanecem pendentes sob o controlador existente e exigem envio
explícito depois do settlement. Uma correção de objetivo passa por nova
admissão, em vez de reescrever silenciosamente a definição antiga.

## Campanha e limites de medição

A campanha em `tests/fixtures/predictability/scenarios.json` registra objetivos,
históricos e resultados esperados. Os testes correspondentes ficam em
`tests/unit/planning/test_predictability_campaign.py`, acompanhados dos testes
de conclusão, contexto e projeção compartilhada.

Os oráculos distinguem admissão de validação e sucesso terminal. Um helper de
validação aceitar um histórico vazio não significa que uma tarefa vazia esteja
concluída: o revisor canônico ainda verifica os efeitos e as obrigações pendentes.
O fluxo com arquivos reais exercita validação, rollback/reparo e conteúdo final;
o modelo é substituído somente na borda por respostas controladas.

Resultados com gateway scripted são regressões determinísticas, não uma
estimativa de acurácia de um modelo real. Taxa de perguntas desnecessárias,
latência/tokens de modelos reais e generalização para pedidos inéditos requerem
avaliação separada com endpoint e modelo identificados. Não se deve extrapolar
o número de cenários aprovados para uma garantia universal de autonomia.

## Resultado local em 26/10/2026

| Medição | Antes | Depois |
| --- | --- | --- |
| Oráculos atendidos na admissão de validação | 15/27 | 27/27 |
| Admissões indevidas de validação nessa amostra | 11 | 0 |
| Exceções C901 registradas no baseline do projeto | 24 | 18 |
| Exceções de tamanho registradas no baseline do projeto | 32 | 30 |

O baseline de validação é o arquivo original byte a byte, identificado por
SHA-256 e pelo blob Git no [relatório JSON](history/2026/milestones/resultados-previsibilidade-20261026.json).
A comparação mantém os mesmos oráculos nos dois lados. A divergência restante
do baseline era uma tentativa sem mutação; a revisão de conclusão continua
responsável por exigir que os efeitos solicitados realmente ocorram.

A campanha possui **47 testes**. A validação integrada final terminou com
**1.574 testes aprovados e 2 skips**, em Python 3.12.10 no Windows. Os skips
dependiam de criação de symlinks, indisponível por privilégio do ambiente.
Ruff em `src`, `scripts` e `tests`, mypy Linux/Windows e `git diff --check`
passaram. A arquitetura CURRENT passou com 934 módulos e 3.278 arestas.
Os módulos novos de interpretação têm até 295 linhas e nenhuma exceção nova
de complexidade.

Os gates globais de lint/qualidade continuam com pendências anteriores em
`prototypes/workbench-comparison`: 115 achados de lint, complexidade de fontes
ativas/snapshots e 28 arquivos fora da política de encoding. A rodada estática
iniciada durante a refatoração também capturou uma falha transitória de parse;
a checagem CURRENT final, após estabilização, passou. Essas evidências antigas
não foram reescritas nem excluídas da política para declarar o projeto verde.

Reprodução do comparativo neste workspace:

```powershell
.venv\Scripts\python.exe scripts\run_predictability_campaign.py --baseline-source .audit-local\predictability-baseline-20261026-01\task_completion_constraints.py
```

Sem `--baseline-source`, o runner verifica apenas o candidato atual e retorna
status diferente de zero se divergir de algum oráculo. O snapshot local não é
distribuído como fonte de produção: o relatório conserva a identidade Git e os
hashes dos módulos críticos usados no comparativo. Nenhuma avaliação com modelo
real ou aceitação de release é declarada por esses números.
