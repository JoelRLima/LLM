# Planejamento orientado a resultados

O planejador pode declarar quais requisitos e obrigações cada etapa pretende
atender. O runtime valida essas referências e publica sua relação com os
resultados observados, preservando a separação entre intenção e comprovação.

## Fluxo

1. `TaskDefinitionCompiler` admite e persiste Contract/Spec imutáveis.
2. Nas decisões de planejamento inicial e continuação, o modelo recebe um
   catálogo limitado dos requisitos da definição ativa e das obrigações já
   admitidas. O catálogo identifica também os requisitos visíveis ainda sem
   etapas associadas.
3. Os slots do plano podem incluir `requirement_ids` e `obligation_ids`.
4. Antes da admissão/recuperação do plano e novamente após otimização, o gateway
   verifica os vínculos contra a definição e o owner semântico ativos.
5. A execução mantém seus IDs de etapa, invocação e evidência canônicos.
6. Snapshot e recibo publicam `plan_coverage`, relacionando os requisitos às
   etapas e aos fatos de execução. A revisão de conclusão continua pertencendo
   aos verificadores existentes.

Uma referência de requisito desconhecida, de outra definição ou de uma versão
antiga é rejeitada antes de invocar ferramentas. O catálogo também deve pertencer
ao workspace atual. IDs de obrigação precisam existir em `TaskSemantics`;
o modelo não cria autoridade ao inserir uma string no plano.

## Contrato dos vínculos

`ToolPlanStep` e `DeferredConditionStep` aceitam os dois campos opcionais na
raiz do slot. Cada campo presente é uma lista de 1 a 16 IDs únicos, com até 128
caracteres por ID. Lista vazia, `null`, booleano, string no lugar da lista e IDs
vazios são rejeitados. Não há campos de sucesso, status ou evidência aceitos
como parte dessa declaração.

Use os IDs exatos do catálogo ativo; não os invente nem reutilize de outra
tarefa. Em condições diferidas, os vínculos pertencem ao slot da condição,
não ao objeto `on_true`. Sua materialização preserva as associações.

A serialização permanece compatível quando os campos estão ausentes. Planos
legados continuam executáveis e seus requisitos aparecem como `unmapped`.
Otimização, bindings, reparos de validação, checkpoints e extensão do plano
preservam os vínculos. Passos com associações diferentes não são descartados
como se fossem duplicatas equivalentes.

## Como interpretar a cobertura

| Campo | Significado |
| --- | --- |
| `status: linked` | Existe ao menos uma etapa declaradamente associada ao requisito. |
| `status: unmapped` | O plano não declarou etapa para esse requisito. |
| `verification_status` | `unverified` sem condição estruturada; `satisfied`, `violated` ou `inconclusive` para as condições declaradas. |
| `step_status` | Estado operacional da etapa, separado da verificação do requisito. |
| `invocation_refs` e `evidence_refs` | Observações correlacionadas ao ID da etapa e à identidade do plano. |
| `canonical_statuses` e `canonical_evidence_refs` | Estado e evidências das obrigações declaradas, obtidos do owner `TaskSemantics`. |
| `unresolved_requirement_ids` | Requisitos sem condições declaradas satisfeitas, inclusive os já vinculados a etapas. |
| `unmapped_requirement_ids` | Subconjunto sem associação declarada no plano. |

Concluir uma etapa não comprova automaticamente seu critério textual. Uma
obrigação pode ter evidência canônica própria, enquanto o requisito livre
continua `unverified` quando não possui condições estruturadas. Referências operacionais e evidências semânticas são
publicadas em campos separados para não sugerir uma relação causal que o
runtime não demonstrou.

O campo não aumenta a autoridade do plano, não dispensa validação e não promove
falha ou rollback a sucesso. Na implementação atual não há um verificador
genérico de qualquer frase de Contract/Spec. Uma avaliação com modelo real é
necessária para medir eventual melhora na taxa de conclusão; os testes locais
comprovam o contrato e os comportamentos cobertos, não essa melhora geral.

## Condições verificáveis de arquivo e JSON

O Contract aceita opcionalmente `verification_checks`. Cada condição aponta
para `requirements[index]` ou `success_criteria[index]` do próprio Contract,
com índice começando em zero. A condição participa do digest imutável da
definição; os IDs dos requisitos são resolvidos depois da admissão.

```json
{
  "success_criteria": ["Entregar result.json com o campo answer"],
  "verification_checks": [
    {
      "check_id": "result-shape",
      "kind": "json_fields",
      "field": "success_criteria",
      "index": 0,
      "path": "result.json",
      "required_fields": ["answer"]
    }
  ]
}
```

Esse trecho deve integrar um Contract completo. `file_exists` exige um arquivo
regular no caminho indicado; `json_fields` exige JSON válido, objeto na raiz e
presença das chaves declaradas nesse objeto. O valor `null` conta como presença.
A condição não valida tipos, valores, objetos aninhados nem um JSON Schema.
Campos extras são permitidos; chaves duplicadas e constantes como NaN são
rejeitadas. A leitura de JSON é limitada a 1 MiB em UTF-8.

São permitidas até 64 condições, com IDs únicos. Caminhos usam `/`, são
relativos ao workspace e não aceitam caminhos absolutos, drives ou `..`.
Symlinks e reparse points são recusados. Falta de arquivo ou condição falsa
produz `violated`; erros de acesso e limites de leitura produzem `inconclusive`.

O runtime observa os arquivos novamente nas fronteiras de planejamento,
conclusão e publicação. Não reutiliza um sucesso anterior após edição do
arquivo. A revisão de conclusão exige todas as condições satisfeitas, mantendo
prioridade das falhas e obrigações canônicas existentes. Planos sem condições
preservam o comportamento anterior. Esta entrega adiciona condições ao
Contract; critérios específicos de fases da Spec continuam textuais.

O recibo publica `plan_coverage.requirement_verification`, com identidade da
definição, horário UTC, motivo e SHA-256 dos bytes de JSON lidos, sem conteúdo
do arquivo. A publicação final é congelada. Os estados verificados têm
`verification_scope: declared_checks_only`: comprovam somente as condições
estruturadas, não toda a frase associada, autoria do arquivo ou correção geral
da tarefa. Várias condições para uma frase precisam ser satisfeitas juntas.
As pendências são fornecidas ao planejador como contexto limitado.

## Replanejamento das condições pendentes

Ao esgotar um plano, o runtime consulta a revisão canônica de conclusão antes
da fronteira genérica de raciocínio. Quando o motivo prioritário é uma condição
estruturada violada ou inconclusiva, pode solicitar um plano de correção. Falhas
operacionais, cancelamento, efeitos proibidos e obrigações canônicas pendentes
mantêm sua prioridade; uma condição de arquivo não os substitui.

A tentativa consome `RecoveryScope.LLM_REPLANS`, compartilhado com os demais
replanejamentos. A política padrão permite uma tentativa nesse escopo e mantém
o teto agregado de dois replanejamentos heurísticos/LLM por tarefa. Limites
quantitativos e cancelamento continuam sujeitos à política existente. Não há
um orçamento extra nem reinicialização dos contadores para corrigir requisitos.

O planejador precisa retornar `execute` com etapas admitidas. Uma resposta
`complete` não dispensa a condição que falhou. A extensão passa pelo gateway
normal de validação e pelas permissões atuais, e suas etapas são executadas
pelo executor existente. Ao terminar, os arquivos são observados novamente;
somente a revisão canônica pode aceitar a conclusão.

O runtime persiste fingerprints das condições que já motivaram uma tentativa.
Eles incluem a identidade exata da definição, IDs das condições e seus
estados/motivos, excluindo horário e hash dos bytes observados. Assim, alterar
o conteúdo sem resolver a mesma condição não permite repetir indefinidamente
a tentativa. O progresso é medido por condição, não pela quantidade de bytes
alterados. Checkpoints preservam esse histórico e o orçamento; uma tarefa nova
reinicia o histórico. A lista tem limite de 64 entradas e nunca descarta uma
entrada antiga para autorizar mais tentativas.

Sem progresso, sem orçamento, sem definição observável ou sem um plano válido,
a tarefa permanece bloqueada com um motivo explícito. O mecanismo não amplia
o escopo do Contract, as ferramentas apresentadas ou a autoridade de escrita.

## Limites e publicação

O catálogo do planejador tem até 32 entradas, textos de até 240 caracteres e
limite de 8.000 caracteres para seu JSON completo, incluindo obrigações e resumo
de verificação; omissões são explícitas, inclusive em `omitted_obligations`. Ele é
adicionado às fronteiras de planejamento, sem duplicar o catálogo completo em
todas as chamadas ao modelo.

A projeção de inspeção limita listas a 64 itens e textos de requisito a 500
caracteres. Totais, contagens omitidas e `truncated` distinguem exposição parcial
do cálculo integral. A correlação examina o histórico completo disponível e
exclui observações de outro plano. A publicação em snapshots/recibos aplica a
redação de credenciais existente e congela os valores.

## Verificação

Os testes de integração estão em
[`test_outcome_planning.py`](../tests/integration/test_outcome_planning.py):
leitura real com associação, bloqueio de referências desconhecidas/obsoletas
antes de ferramentas, compatibilidade legada, falha de mutação com rollback e
checkpoint seguido de extensão do plano. Os IDs esperados são obtidos da
definição persistida, independentemente da resposta do gateway scripted.

Os testes de contrato/projeção também cobrem limites, tipos malformados,
preservação em otimizações/reparos, estados operacionais, isolamento de planos,
omissões explícitas, contexto de planejamento e redação de dados publicados.

Na validação inicial dos vínculos em 26/10/2026, a seleção de regressão terminou com **1.917 testes
aprovados e 2 pulados**, ambos por indisponibilidade de criação de symlinks no
Windows. Ruff em `src`, `scripts` e `tests`, mypy para Linux e Windows e
`git diff --check` passaram. O gate global de qualidade continua bloqueado pelas
pendências anteriores de complexidade e UTF-8 com BOM em
`prototypes/workbench-comparison`; nenhuma exceção foi adicionada para ocultá-las.

Na entrega dos verificadores, a regressão cobriu 1.951 testes: 1.948 passaram,
2 foram pulados por limitações de symlinks e o cenário de extensão stdio falhou
ao exceder seu timeout de 5 segundos. A reexecução isolada desse cenário passou
em 1,40 segundo, totalizando 1.949 casos aprovados entre as execuções. A suíte
de definição de tarefas também passou após o ajuste final do prompt (77
aprovados, 1 pulado). Ruff e mypy para Linux/Windows passaram; o gate global
de qualidade continua apontando somente as pendências anteriores dos
protótipos. Os testes usam gateway determinístico com filesystem real, sem
medir taxa de sucesso de um modelo real.

Na entrega do replanejamento, a regressão passou com **1.963 aprovados e 2
pulados** por limitações de symlinks. Os três cenários adicionais de
[`test_requirement_replanning.py`](../tests/integration/test_requirement_replanning.py)
também passaram: correção por duas edições reais via `code_task`, interrupção
de tentativa repetida e recusa de escrita em uma tarefa de leitura. O SHA-256
publicado foi comparado aos bytes finais do workspace; os prompts registraram
a transição de `violated` para `satisfied`. Os testes de limites também cobrem
checkpoint, falhas repetidas com horário/hash alterados e propagação de
`BudgetExhausted`. Ruff, mypy para Linux/Windows e `git diff --check` passaram.
O gate global de qualidade mantém somente as pendências anteriores em
`prototypes/workbench-comparison`. Não foi feita avaliação com modelo real.

Consulte [previsibilidade de tarefas](previsibilidade-tarefas.md) para as regras
de validação atual por recurso e os owners de conclusão.
