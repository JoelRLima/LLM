# Disponibilidade — correção final

P1 corrigido: ausência de configuração recusa submissões naturais e de agente antes de `_ensure_runtime`; `/ls`, Arquivos e Tarefas continuam locais. O botão de teste sem perfil retorna o estado atual sem abrir conexão.

P2 corrigido: falha normalizada de conexão ou timeout ocorrida durante a execução mantém o bloqueio até recuperação manual. Todos os resultados inconclusivos de `/models` preservam esse bloqueio; sem falha anterior, permanecem como `unverified` e não impedem uma submissão. Timeouts e erros de transporte exclusivos do catálogo não são projetados como falha comprovada de inferência. HTTP 401/403, 404, 405/501, 5xx e metadados não utilizáveis são inconclusivos. 405/501 indicam que o método GET nãoé suportado. 404 nãoé classificado automaticamente como rota inexistente, pois pode refletir prefixo, proxy ou política do provedor.

HTTP 200 verifica apenas acesso ao catálogo. A estrutura precisa conter `data` como lista de entradas com `id` textual não vazio. JSON vazio, erro do provider e entradas malformadas são rejeitados como diagnóstico inconclusivo. Modelo ausente da lista ou presente nela mantém prontidão de inferência como não verificada. `INTERACTION_RESOLVER_UNAVAILABLE` e `INTERACTION_RESPONSE_UNAVAILABLE` não são evidência causal de falha de transporte e não alteram disponibilidade.

## Validação desta correção

- 56 testes Python dirigidos passaram: matriz com/sem bloqueio anterior para HTTP 401/403/404/405/501/503, timeout, falha de leitura e metadados inválidos; submissão natural permitida quando inconclusivo sem bloqueio; submissão recusada quando existe bloqueio; HTTP 200 sem falsa prontidão; códigos genéricos sem alteração de estado. Inclui os 29 testes anteriores de sessão e integração vertical.
- Os três testes JavaScript passaram: 17 casos de submissão, 12 de painel, botões e backoff. Nenhum texto de polling online foi acrescentado.
- Ruff passou; `W17_ARCHITECTURE=PASS`; `git diff --check` passou. Sem endpoint, inferência real ou Qwen.

Logs finais em `evidence/availability-final-20261022/pytest-delivery.log`, `pytest-final6.log`, `pytest-matrix.log`, `frontend-matrix.json`, `ruff-delivery.log`, `architecture-delivery.log` e `diffcheck-delivery.log`. Logs das tentativas anteriores continuam preservados. Baselines e evidências históricas não foram alteradas.

Relatório em UTF-8. A aceitação visual continua pendente.
