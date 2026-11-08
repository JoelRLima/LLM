# Correção após auditoria independente — 21/10/2026

Branch: `fix/cli-ux-refinement`, base `9b5a2ddc2645430142e3c1ecaf6ef95c41e40406`.
Escopo: contrato de cancelamento, seleção por nome e esclarecimentos de
encoding/aviso de workspace. Nenhuma GUI foi iniciada. Publicação, merge e
aceitação formal da W23 permanecem pendentes.

## Achados e correções

**P1 confirmado.** A rodada anterior preservou `None` no seletor de modelos,
mas deixou consumidores que o transformavam em Enter. `read_prompt()` agora
preserva três resultados: valor informado, `""` (Enter) e `None` (cancelamento).
Captura também EOF/KeyboardInterrupt em leitores de console ou injetados e
preserva leitores legados que aceitam somente a mensagem. Não cria outro
dono de stdin.

- Setup: cancelar a confirmação inicial não cria configuração. Cancelar
  perfil, modelo ou endpoint não aplica ajustes nem marca o setup como
  concluído. A configuração padrão criada após confirmação explícita é
  preservada, e a sessão não entra automaticamente no chat.
- Thinking: cancelar não altera o orçamento. Enter, presets, números e a
  resposta inválida mantêm o fallback existente; o comando que desliga um
  orçamento já ativo continua funcionando como antes.
- Histórico: cancelar save/load não lê nem grava o arquivo padrão e não
  altera as mensagens. Enter continua usando o caminho padrão.
- Memória e prompt de sistema: os mesmos guardas impedem efeitos após
  cancelar. O adaptador de caminhos do workspace também preserva `None`;
  o fallback de console retorna `WorkspaceSelectionCancelled` em EOF/Ctrl+C.
- Retorno ao chat: não há conversão de cancelamento em saída da sessão
  pelos handlers cotidianos. O setup incompleto encerra sua recuperação
  sem iniciar a sessão. Aprovações, grants e authority não foram alterados.

**P2 de rótulos confirmado.** O seletor resolve IDs exatos únicos antes de
rótulos. Rótulos exatos repetidos geram ambiguidade; uma opção indisponível
também participa dessa ambiguidade. A pessoa pode escolher pelo número ou
pelo ID. A paleta continua apenas preparando rascunhos.

**P2 de CRLF/LF investigado.** A divergência está no blob base, não apenas
no checkout:

| Referência | Conteúdo de `first_run.py` |
| --- | --- |
| W22 `e61faff` | 184 linhas LF, zero CRLF |
| Base W23 `9b5a2dd` | 177 linhas CRLF, zero linhas LF isoladas |
| Candidato atual | UTF-8 sem BOM, LF |

`git ls-files --eol` informa `i/crlf w/lf`; `git check-attr` confirma
`text=auto eol=lf`, inclusive na referência W22. A configuração de sistema
do Git informa `core.autocrlf=input`. Assim, a entrada CRLF foi introduzida
no commit de integração W23 apesar da política LF. O mecanismo que gravou
esse blob (editor, comando ou ferramenta) não pode ser determinado apenas
pelo histórico disponível; não foi atribuído por suposição.

A normalização LF é mantida e documentada: restaurar CRLF para reduzir o
diff contrariaria a política canônica. Nenhum atributo, commit histórico,
hash ou registro de aceitação foi alterado. Além da normalização e da
correção de mojibake anterior, agora há alterações funcionais no setup.

**P3 tratado como aviso.** A heurística foi restringida ao componente na
raiz convencional do Windows. Projetos como `D:\projetos\Windows\demo` ou
`D:\projetos\Program Files\demo` não são marcados. Ela continua aproximada,
não cobre toda pasta sensível e não determina confinamento ou permissão.
A escolha explícita continua permitida após o aviso.

## Validação desta rodada

Os testes novos exercitam `None`, EOF, KeyboardInterrupt, Enter, resposta
inválida e fallback de leitores legados. Usam configuração/histórico reais
em diretórios descartáveis e `ChatSession` com gateway offline; a prova
de ausência de escrita compara bytes e mensagens. Quatro cenários de
Prompt Toolkit recebem Ctrl+C real por pipe e depois enviam outra mensagem
no mesmo shell para demonstrar retorno ao chat.

- Regressão direcionada final: **458 aprovados, 9 ignorados**, incluindo
  todos os testes de interfaces, catálogo, modos operacionais, diagnóstico
  standalone e compatibilidade de ações/queries da W19. Foram acrescentados
  **62 casos** neste ciclo. Caminho e conteúdo inválidos do histórico também
  são exercitados com I/O real e falha explícita, sem usar o arquivo padrão.
- Ruff aprovado; mypy Linux e Windows aprovados, 984 arquivos verificados.
- Qualidade aprovada em worktree isolado, sem relaxar limites ou baseline.
- Wheel em ambiente limpo aprovado: `Installed package clean acceptance passed.`.
- `git diff --check` aprovado. Nenhum Qwen ou endpoint de modelo real usado.

Evidências locais: `.tmp/ux-audit-regression.log`, `.tmp/ux-audit-ruff.log`,
`.tmp/ux-audit-mypy-linux.log`, `.tmp/ux-audit-mypy-win.log`,
`.tmp/ux-audit-quality.log`, `.tmp/ux-audit-installed.log` e
`.tmp/ux-audit-installed-summary.json`. O candidato isolado está em
`.tmp/ux-audit-validation`, com os bytes copiados do checkout e comparados.

Entrega incremental: `reports/ux-audit-correction-20261021.patch` e
`reports/ux-audit-correction-20261021.manifest.json`. A base desse patch é a
cópia de validação da rodada anterior preservada em `.tmp/ux-validation`.
O manifesto discrimina hashes antes/depois e o hash do patch. Isso não
constitui uma nova verificação do ZIP citado pela auditoria independente.

## Parecer

**P1 e a ambiguidade de rótulos corrigidos e revalidados. GO para testes
exploratórios offline da CLI e campanha funcional controlada de UX.** A
normalização LF está justificada e o aviso de workspace permanece apenas
orientativo. Esta rodada não herda um resultado global verde: a suíte da rodada
anterior tinha 6007 aprovados, 72 falhas, 59 ignorados e 5 erros, classificados
no relatório anterior. Ela não será apresentada como aprovada por causa de
uma repetição direcionada. As provas históricas da W23 permanecem intactas.

A suíte global não foi reexecutada neste ciclo focado. A prontidão para
merge/publicação e a aceitação W23 continuam bloqueadas pela validação global
pendente e pelas verificações vinculadas a inventários históricos; não foram
aprovadas por este relatório. Persistem os testes práticos de terminal/modelo
real e a revisão de textos avançados/backend já registrados na rodada anterior.
Uma eventual GUI deverá usar branch própria e preservar os owners neutros
de aplicação; não faz parte desta correção.
