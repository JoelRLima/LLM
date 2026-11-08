# Disponibilidade e recupera??o controlada

A proje??o tipada est? em `llm_agent.interactive.availability`: estado do backend, configura??o, conectividade, prontid?o observ?vel do modelo, identidade/gera??o e estados de recursos. N?o substitui o estado operacional do controller nem o suporte declarado pela bridge.

A configura??o ? resolvida pelo ConfigRepository/perfil can?nico. Resolver configura??o n?o verifica conectividade. Mudan?as de fingerprint invalidam o resultado anterior; o runtime ? recriado na pr?xima opera??o quando a identidade mudar. O seletor gr?fico de perfil continua n?o implementado. Erros normalizados ModelConnectionError/ModelTimeoutError alteram disponibilidade; falhas comuns de tarefa n?o alteram. INTERACTION_RESOLVER_UNAVAILABLE/INTERACTION_RESPONSE_UNAVAILABLE exigem reavalia??o, mas sua causa ? declarada n?o verificada.

Depois dessas falhas, novas submiss?es dependentes do modelo s?o recusadas at? reavalia??o manual. Nenhuma infer?ncia ? repetida para recuperar a sess?o. Arquivos, Tarefas e consultas locais continuam utiliz?veis. O bot?o de disponibilidade oferece Testar conex?o: uma requisi??o GET ao cat?logo /models, leitura m?xima de 256 KiB, timeout de transporte de at? cinco segundos, sem completions. O teste usa o mesmo worker serializado e n?o inicia timer peri?dico. Cat?logo alcan??vel ? conectividade verificada; modelo listado ? somente listado, n?o infer?ncia validada. Aus?ncia no cat?logo mant?m prontid?o n?o verificada.

## Limita??es expl?citas

O probe de metadados implementa endpoints HTTP OpenAI compatible. N?o reaproveita credenciais secretas de gera??o: cat?logos que exigem autentica??o podem retornar sem permiss?o mesmo quando a gera??o autenticada funcionaria. Cat?logos ausentes/HTTP incompat?vel s?o temporariamente indispon?veis para esse teste, n?o prova de que o servi?o inteiro est? inacess?vel. Timeout de transporte n?o ? deadline absoluto de resolu??o DNS. Uma resposta de infer?ncia bem-sucedida n?o ? necess?ria para disponibilizar consultas locais. O CLI pode consumir a proje??o compartilhada, mas sua apresenta??o de terminal n?o foi redesenhada nesta fatia. Recursos gr?ficos ainda n?o implementados permanecem identificados como indispon?veis no prot?tipo; a aprova??o continua sem permiss?o em READ_ONLY.

## Valida??o e regress?es

`evidence/availability-recovery-20261022/regression-final.log`: **226 passaram, 1 skip**. Regress?o ampla dos componentes extra?dos do CLI: controller, aten??o, apresenta??o, consultas, parser/completion, compatibilidade, cancelamento, renderiza??o; inclui Tk, bridge, configura??o e integra??o can?nica com gateway de teste. N?o ? execu??o da totalidade dos testes do reposit?rio.

Falhas hist?ricas finais na sele??o: nenhuma; skip hist?rico de symlink preservado. Novas regress?es finais detectadas: nenhuma. N?o foram alteradas baselines. Erros intermedi?rios de harness de frontend/encoding foram corrigidos; o log frontend.json preserva a tentativa intermedi?ria, e buttons-polling-final.json cont?m a aprova??o final de bot?es/polling. Os outros dois conjuntos de frontend passaram em frontend.json: 17 cen?rios de submiss?o e 12 de painel/stream/cancelamento. Auditoria atual: 18 bot?es, 14 a??es exercitadas, 4 indispon?veis, nenhum habilitado sem a??o. Polling persistente testado com timers controlados: 2,4,8,16,30,30 segundos, sem sleeps de teste. Ruff e arquitetura W17 passaram.

Os testes novos usam configura??o/perfil e probe injetados, sem sockets: configura??o ausente, conex?o recusada, timeout normalizado, recupera??o manual sem nova infer?ncia, mudan?a de identidade, consultas locais e separa??o entre erro de tarefa e backend. O bloqueio ap?s falha ? verificado em tr?s novas tentativas sem aumento da contagem de execu??o. Nenhuma verifica??o online real ou carregamento de modelo foi feito nesta rodada.

## Reprodu??o offline

1. `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/unit/interfaces/test_availability.py tests/unit/interfaces/test_shared_interactive_core.py`.
2. Com Node compat?vel, execute os arquivos `test_webview_submission.mjs`, `test_webview_ui.mjs` e `test_webview_buttons.mjs` na pasta do prot?tipo. O teste de polling usa fila de timers controlada.
3. Abra `.venv/Scripts/python.exe prototypes/workbench-comparison/run_workbench.py --workspace <pasta> --home <home-sem-configura??o>`. Confira N?o configurado e consulte Arquivos/Tarefas. Testar conex?o nesse estado n?o deve fazer requisi??o.
4. Para reproduzir recusa sem modelo, use configura??o de teste apontando para uma porta localhost fechada e clique Testar conex?o uma vez. Confirme indisponibilidade, consultas locais e aus?ncia de novas verifica??es enquanto a janela permanece aberta. Reavaliar exige novo clique. Para recupera??o determin?stica sem servi?o externo, use os testes injetados do passo 1.

Tk, contratos de seguran?a, READ_ONLY e evid?ncias hist?ricas preservados. Sem Qwen, commit, merge ou push. Aceita??o visual permanece com o usu?rio.
