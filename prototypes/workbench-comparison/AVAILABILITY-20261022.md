# Investigação — indisponibilidade no WebView2

Foram confirmadas duas lacunas:

1. `consume()` agendava a próxima rodada de demo após qualquer settlement, mesmo `failed`, `blocked` ou cancelado. Não havia retry automático da submissão manual nesse frontend. O polling periódico de `wait_events` consulta eventos da bridge; não é uma tentativa de geração de modelo.
2. Arquivos, Tarefas e Ferramentas eram botões estáticos sem handlers ou painéis correspondentes. O contador `1` de Tarefas era fixo, não um inventário consultado. A bridge expõe submissão, cancelamento, polling e leitura dos relatórios oferecidos; não expõe navegação desses três recursos.

## Modelo

O protótipo atual não conecta modelo real. Com `--offline-fixture`, simula a sessão na fronteira da bridge. Sem essa opção, `fixture.py` usa a application real com `JourneyGateway` offline. Portanto, a indisponibilidade de um modelo real **não foi reproduzida** nesse protótipo e não pode ser deduzida de um erro genérico. A interface não oferece consulta de saúde/disponibilidade de modelo real. Nenhum modelo foi carregado nesta investigação.

## Ajustes aplicados

- A demo só avança automaticamente com status canônico `succeeded`. Falha, bloqueio, cancelamento, status desconhecido e erro da bridge encerram as rodadas pendentes. Uma nova solicitação manual continua possível. Não foram alterados os retries internos da application nem inventado um reason code de indisponibilidade.
- Arquivos, Tarefas e Ferramentas agora são botões desabilitados com indicação visível **Indisponível** e explicação de que não estão implementados neste protótipo. Foi removido o contador fictício de Tarefas; não foram criados novos painéis.
- O indicador superior agora é um botão de estado desabilitado **Modelo real não conectado**. Sua descrição informa que a execução usa fixture offline e não verifica a disponibilidade de modelos reais. Ele não se apresenta como uma consulta ou ação de reconexão.

## Validação

Passaram **17 cenários de submissão/disponibilidade**, **12 cenários anteriores de frontend** e **5 testes de bridge/fixture**. Os sete cenários adicionados cobrem interrupção da demo em failed/blocked/cancelado/desconhecido/erro, continuidade após sucesso e sinalização dos botões sem contador fictício. São verificações determinísticas com promessas e callbacks controlados, sem sleeps ou modelo real.

```powershell
node prototypes/workbench-comparison/test_webview_submission.mjs
node prototypes/workbench-comparison/test_webview_ui.mjs
.venv/Scripts/python.exe prototypes/workbench-comparison/test_webview_bridge.py
```

Os arquivos JavaScript foram executados via runtime Node de `node_repl` neste ambiente. Resultados novos em `evidence/availability-20261022/`. Os registros anteriores permanecem intactos. Tk, application, contratos e fixture compartilhada não foram alterados; não houve commit, merge, publicação ou mudanças de permissões.

Reabra o protótipo para carregar os arquivos atualizados. Conferir visualmente os três botões, o indicador superior e a retirada do contador de Tarefas. Não houve nova inspeção visual nesta rodada. A repetição relatada pelo usuário pode precisar de investigação adicional se ocorrer fora do modo demo ou em outra interface: o achado reproduzível aqui é o avanço automático da campanha após settlement sem sucesso.
