# Modernizações de 27/10/2026

Esta rodada revisou as fronteiras do agente e corrigiu problemas observáveis,
preservando os contratos de autoridade, os planos existentes e o orçamento de
recuperação. A auditoria e as correções de planejamento, armazenamento e
avaliação foram divididas entre subagentes Luna.

## Contexto e verificação de requisitos

O limite de 8.000 caracteres agora cobre o JSON completo do catálogo enviado ao
planejador: requisitos, obrigações e resumo de verificação. Antes, só as linhas
dos requisitos entravam na conta. As contagens `total_obligations` e
`omitted_obligations` tornam explícita a omissão de obrigações. A moldura e as
instruções textuais ficam fora desse limite do JSON.

A verificação de arquivos também rejeita caminhos com `:`, componentes vazios
ou `.` na própria fronteira de filesystem. Isso cobre formas de drive/ADS no
Windows mesmo quando um chamador interno contorna a validação do Contract.

## Persistência do Discovery

O documento de frequência e recência é lido com limite de 128 KiB. IDs lidos
e registrados respeitam o limite existente de 192 bytes UTF-8. Documentos
inválidos continuam produzindo `DISCOVERY_FRECENCY_CORRUPT`.

O store preserva o caminho lexical absoluto, em vez de seguir links durante
a construção. As primitivas compartilhadas de segurança verificam os
componentes do caminho antes de leitura, criação de diretórios e aquisição do
lock. Assim, um symlink não desaparece da inspeção por ter sido resolvido
antecipadamente. Tentativas de gravação em caminhos inseguros são recusadas.

## Métricas de avaliação

Um conjunto vazio agora informa `total=0` e `pass_rate=0.0`, em vez de 100%.
A política de repetições rejeita limites impossíveis, valores não inteiros e
booleanos usados como contagens. O número de aprovações precisa estar entre
zero e o número de amostras válidas; a mensagem de erro usa o tamanho de amostra
configurado.

## Respostas e conexões do provider

O parsing foi extraído para `agent/llm/providers/openai_response.py`, mantendo
os métodos públicos do gateway. Estruturas inesperadas de `choices`, `message`,
`delta` e texto produzem `ModelResponseError`, sem converter objetos em
respostas textuais. UTF-8 inválido e JSON inválido em linhas de dados do stream
também falham explicitamente; o socket é fechado e nenhum `DONE` normal é
emitido após essas falhas.

Contagens fracionárias, negativas, booleanas ou não finitas não são promovidas
a inteiros de uso. Strings de inteiros decimais continuam compatíveis. A
tokenização opcional fecha a resposta HTTP tanto no sucesso quanto em erro,
JSON inválido ou endpoint não suportado. Ausência de `tokens` retorna
indisponibilidade, não uma contagem artificial de zero. Timeout e interrupção
de conexão durante streaming usam os erros tipados do domínio.

## Confirmação de gravação da observação

O `flush()` do trace confirma uma fronteira capturada na chamada: a geração
dos metadados e a sequência aceita precisam estar publicadas. Heartbeats
posteriores não prolongam indefinidamente essa confirmação. A sequência
confirmada vem de registros persistidos, não da aceitação em memória; uma
gravação ainda bloqueada não pode ser declarada concluída. Falhas do writer e
encerramento sem dreno continuam preservando a classificação de trace incompleto.

## Gates arquiteturais e fixtures

O checker de admissão acompanha os módulos que passaram a implementar as
proteções, em vez de procurar todo o código na antiga fachada de guards.
Testes de mutação retiram deliberadamente essas proteções e exigem que as
regras continuem detectando as violações.

A política de dependências registra as ligações canônicas entre definição,
planejamento, verificação, relatórios e checkpoint por pares específicos de
módulos. A revisão mantém as direções inversas e consumidores vizinhos sem
registro bloqueados, sem alterar o baseline congelado.

As fixtures dos checkers de produto copiam seus arquivos de entrada, incluindo
código Python, metadados e workflows necessários. Dados temporários,
relatórios e evidências locais não entram nessas cópias. Os testes positivos e
de injeção de dependências continuam verificando os mesmos limites.

## Validação

Os testes usam filesystem real nas fronteiras de armazenamento e requisitos,
e respostas HTTP controladas para o provider. A validação não mede qualidade
de um modelo real.

A seleção original de **3.464 casos** foi concluída em etapas: **3.445
aprovados, 19 pulados, nenhuma falha pendente e nenhum caso sem verificação**.
A primeira execução parou em oito falhas, após 2.701 aprovações e 14 pulos.
As falhas levaram aos ajustes de gates e flush descritos acima e foram
reexecutadas. A continuação fora de runtime terminou com 675 aprovações e 2
pulos; a revalidação comportamental de runtime/trace teve 115 aprovações e 3
pulos, incluindo casos repetidos e novos. Essas contagens por etapa não devem
ser somadas como testes distintos. Os pulos refletem restrições da plataforma
e recursos opcionais dos ambientes de teste.

O [relatório consolidado](resultados-modernizacao-20261027.json) registra a
deduplicação da seleção original e a evidência dos rechecks arquiteturais.
Também passaram os testes focais adicionais de fronteiras, políticas exatas
e corridas de gravação.

Ruff em `src`, `scripts` e `tests`, mypy para Linux e Windows e
`git diff --check` passaram. O perfil oficial `scripts/validate.py static`
terminou com **3 de 5 gates aprovados**: Ruff global e a política global de
qualidade continuam apontando somente as pendências anteriores em
`prototypes/workbench-comparison`. Não foram adicionadas exceções ao baseline
para ocultar essas pendências.

Contratos atuais: [planning](../../../agent/planning.md), [LLM](../../../agent/llm.md),
[evaluation](../../../agent/evaluation.md) e
[planejamento por resultados](../../../planejamento-por-resultados.md).
