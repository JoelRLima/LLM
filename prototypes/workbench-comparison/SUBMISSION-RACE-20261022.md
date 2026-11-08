# WebView2 — correção incremental da confirmação tardia

O frontend agora cria um contexto por submissão, com geração, identidade de request e marcador terminal. Os fluxos manual e demo compartilham `dispatchSubmission`. Uma confirmação só pode ativar o contexto que a originou, enquanto ele ainda não recebeu terminal. Settlement/erro encerra esse contexto antes de atualizar a apresentação; confirmações ou rejeições tardias não podem reativá-lo nem modificar uma geração posterior.

Quando o terminal chega antes da confirmação, sua identidade é associada ao contexto pendente. Isso usa a garantia existente da bridge: uma solicitação admitida por vez, eventos FIFO e nenhuma nova admissão com eventos pendentes. Após a confirmação, terminais de outra identidade são ignorados. Não há sleeps, temporizadores novos, alterações na bridge ou histórico ilimitado de requests para resolver a corrida.

O estado ocupado é limpo antes da apresentação do settlement. Assim, `Relatório disponível` permanece visível; cancelamento com relatório mostra `Execução cancelada · Relatório disponível`. Status e reason code canônicos não mudaram.

## Validação

Todos os testes dirigidos passaram:

- **10 cenários novos JavaScript:** settlement antes da confirmação; erro antes da confirmação; recusa e nova submissão; duas gerações consecutivas — nos fluxos manual e demo; rejeição tardia de Promise antiga; duas execuções rápidas com ambas as confirmações em ordem inversa. Conferem busy, identidade ativa, Enviar/Parar e indicação de relatório. Promessas são resolvidas/rejeitadas explicitamente, sem sleeps.
- **12 cenários anteriores do frontend:** cancelamento reutilizável, corridas e falhas de leitura, fechamento/reabertura do painel, identidade do artefato, metadados e truncamento.
- **5 testes anteriores da bridge/fixture:** cancelamento/settlement, fila limitada, erros, metadados e streaming offline.

Reprodução a partir da raiz do repositório:

```powershell
node prototypes/workbench-comparison/test_webview_submission.mjs
node prototypes/workbench-comparison/test_webview_ui.mjs
.venv/Scripts/python.exe prototypes/workbench-comparison/test_webview_bridge.py
```

Neste ambiente, os arquivos `.mjs` foram executados pelo runtime Node de `node_repl`, pois Node não está no PATH. Resultados em `evidence/submission-race-20261022/javascript-tests.json`, `bridge-tests.log` e `scope-check.json`; diff incremental em `frontend.patch` na mesma pasta.

Não foram detectadas regressões nesses testes. Os testes de frontend usam DOM mínimo e promessas controladas; não representam aceitação visual. Não houve novo ensaio do host, pois esta correção altera somente a coordenação de submissão no frontend.

## Escopo e entrega

O único arquivo existente alterado é `pywebview/workbench.js`, confirmado por comparação com os 30 arquivos do pacote pré-piloto anterior. O novo teste, este relatório e os registros de validação são adições. As evidências anteriores e o pacote anterior permanecem intactos; Tk, application e contratos não foram editados. Nenhum modelo foi carregado; não houve commit, merge ou publicação.

O ZIP `workbench-webview2-submit-race-20261022.zip` é incremental, para extração na raiz do mesmo repositório. Contém o frontend corrigido, o novo teste, este relatório e as novas evidências; utiliza os demais arquivos já presentes.
