# Validação — Workbench Desktop F1

Data: 22/10/2026. Branch: `feat/agent-workbench`. A entrega é uma beta WebView2
instalável em modo `READ_ONLY`; ela não muda a GUI padrão nem substitui a Tk ou a
CLI. A implementação usa `llm_agent.interactive` e os serviços de aplicação
existentes. Não houve inferência real, chamada a Qwen, commit ou publicação.

## O que a beta faz

| Área | Comportamento disponível |
| --- | --- |
| Chat | Submissão natural pelo `InteractiveSession`, streaming de eventos, cancelamento, settlement e solicitações repetidas na mesma sessão. |
| Arquivos | Lista, leitura de texto, busca, status e diff via `ReadOnlyWorkspaceQueryService`; o frontend não lê caminhos arbitrários. |
| Tarefas | Mostra estado corrente e execuções recuperáveis fornecidas pelos serviços compartilhados. |
| Relatórios e outputs | Abre artefatos oferecidos pela execução, com identidade, proveniência, truncamento, digest disponível e completude separados. |
| Ferramentas | Projeta registro, origem, capacidades e diagnóstico de extensões existentes. |
| Disponibilidade | Mostra configuração separada de conectividade; o teste manual consulta `/models`, sem inferir que o modelo esteja pronto. Recursos locais continuam acessíveis sem modelo. |

Em relação ao protótipo, a bridge e os recursos estáticos agora vivem no pacote
Python; o launcher de checkout e o candidato anterior são atalhos de
desenvolvimento/compatibilidade, sem uma segunda implementação da bridge. O
fluxo instalado não inicia demonstração ou fixture. A composição do runtime
continua no núcleo compartilhado e mantém `READ_ONLY`.

## Instalação e execução no Windows

O ambiente precisa do Microsoft Edge WebView2 Runtime. Instale o extra opcional e
execute a entrada instalada com um workspace e a configuração existente:

```powershell
python -m pip install "local-llm-agent[desktop]"
llm-agent-workbench --workspace "D:\meu-projeto" --home "D:\meu-home" --config "D:\meu-home\config.yaml" --profile local
```

`--home`, `--config` e `--profile` são opcionais quando a configuração padrão já
está pronta. Para executar a partir do checkout:

```powershell
python -m pip install -e ".[dev,desktop]"
python -m llm_agent.interfaces.workbench --workspace "D:\meu-projeto"
```

Trocar workspace requer reiniciar a janela com outro `--workspace`.

## Evidências

A regressão offline pode ser reproduzida sem endpoint ou privilégio administrativo:

```powershell
.\.venv\Scripts\python.exe prototypes\workbench-comparison\run_workbench_f1_offline_tests.py
```

O runner usa um diretório temporário dentro do checkout e remove-o após os testes.
Resultado final: **84 passed em 18,48 s**. A suíte percorre chat real de aplicação
com gateway sintético, consulta e leitura segura de arquivos, ferramentas,
relatório, segunda solicitação, ausência de configuração, falha causal de
transporte com bloqueio sem segunda chamada, settlement e fechamento com execução
ativa.

Os testes JavaScript determinísticos cobrem quatro ordens de confirmação e
settlement/erro/recusa/repetição; doze cenários do painel cobrem abertura A/B,
respostas fora de ordem, falha, fechamento pendente, identidade, truncamento,
digest e completude. A verificação de controles encontra 20 botões: 17 habilitados
com ação e três indisponíveis com explicação acessível. As confirmações tardias não
reativam uma solicitação terminal, e `Relatório disponível` permanece visível após
o settlement.

A instalação do wheel foi verificada com `pip wheel`, instalação em diretório
isolado e importação em subprocesso. O módulo da bridge, os arquivos HTML/CSS/JS e
o entry point `llm-agent-workbench` foram encontrados a partir do pacote instalado.
O teste usou o gateway offline; não abriu uma sessão com modelo real.

Ruff passou nos arquivos de produção e testes desta fatia. O gate W17 passou.
`git diff --check` passou. Não foram alterados baselines. Os gates globais W21 e
CURRENT continuam reportando problemas históricos da árvore compartilhada: W21
lista 16 módulos `llm_agent.interactive` sem classificação de owner, com zero
violações do alvo desta fatia e zero novas violações; CURRENT também lista as
mesmas classificações e as verificações preexistentes do registro de ações CLI
(`_default_bindings` ausente e grafo com 0 alvos/5 arestas). Esses dois comandos
retornam código diferente de zero; não foram ajustados para aprovar.

O `ruff check` amplo de `prototypes/workbench-comparison/*.py` também encontra
três problemas de formatação em `test_pre_pilot.py`, fora dos arquivos desta
fatia. O Ruff direcionado, incluindo o bridge, launcher, runner e testes F1,
passou. Nenhum desses resultados foi escondido por alteração de baseline.

## Limitações e inspeção pendente

O histórico de relatórios não é enumerado pelo serviço de tarefas disponível; a
interface informa essa ausência e mantém os relatórios da sessão em Artefatos. A
fatia não edita arquivos, não concede aprovações e não apresenta uma UI de
administração de plugins. A disponibilidade do perfil não prova conectividade, e
HTTP 200 em `/models` não prova prontidão de inferência.

A inspeção visual manual no Windows continua pendente. A validação funcional,
estrutural e do wheel não representa aceitação visual; este relatório não declara
a UI visualmente aceita.
