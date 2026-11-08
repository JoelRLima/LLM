# Relatório de auditoria integral do repositório — 02/11/2026

> A implementação posterior e as correções de interpretação estão no
> [registro de remediação](remediacao-auditoria-integral-20261102.md). Este
> relatório preserva as observações da auditoria inicial.

## Resumo executivo

O repositório tem uma base técnica forte: propósito de produto explícito,
separação entre interface e aplicação, gateway de ferramentas com authority e
approval, extensões com grants/fingerprints, fila de testes, verificação de
resultados e traces com limites de retenção. A camada WebView e o modo CLI local
reutilizam `InteractiveSession`.

Os maiores ganhos agora não vêm de adicionar mais capacidades. Vêm de tornar o
estado atual revisável, fechar a diferença entre intenção de segurança e
isolamento real, medir a qualidade com um modelo identificado e reduzir a
complexidade percebida entre as jornadas.

| Ordem | Prioridade | Tema | Viabilidade |
| --- | --- | --- | --- |
| 1 | **P0 — antes de promover/publicar** | Criar um baseline revisável do checkout | Alta, exige classificação |
| 2 | **P1 — segurança** | Formalizar confiança e isolamento de plugins/processos | Média/baixa |
| 3 | **P1 — privacidade** | Endurecer logs, traces, relatórios e exports | Média |
| 4 | **P1 — confiabilidade** | Atualizar evidência com um epoch real de modelo | Média, requer endpoint e autorização |
| 5 | **P1 — arquitetura/UX** | Fechar paridade de ciclo de vida entre Workbench e CLI | Média, por migração gradual |
| 6 | **P1 — experiência** | Simplificar o primeiro uso e descoberta por tarefa | Alta |
| 7 | **P2 — avaliação** | Converter traces/recibos revisados em casos reutilizáveis | Média |
| 8 | **P2 — operação** | Retomar bateria sem manter a janela aberta | Média/baixa |
| 9 | **P2 — feedback** | Reduzir atraso entre deltas do streaming | Alta, sujeito a backpressure |
| 10 | **P2 — entrega** | Reproduzir instalação e reduzir ruído na raiz | Alta em partes |

## Escopo e método

Revisão estática e estrutural da raiz e das áreas `src/`, `tests/`, `scripts/`,
`docs/`, `quality/`, `distribution/`, `installer/`, `examples/`, `.github/` e
`prototypes/`. Comparei propósito/guias com fronteiras no código, políticas,
CI, pacote e fluxos de execução. Separei referências CURRENT de relatórios
históricos e campanha scripted de evidência com modelo real.

Não li `config.json` ou arquivos locais ignorados que possam conter segredos.
Algumas pastas de evidência/cache negaram acesso e seus conteúdos não foram
auditados. Não fiz revisão linha a linha de cada módulo, execução de testes,
inspeção visual, chamada de rede ou inferência live. Os achados indicam riscos,
limites e oportunidades demonstrados pelos arquivos consultados; não declaram
que todos os fluxos estejam livres de defeitos.

## Achados detalhados

### 1. O checkout não oferece baseline seguro para revisão final — P0

`git status` apresenta **956 entradas**: 194 modificados, 17 removidos e 745 não
rastreados. O diff rastreado reporta 211 arquivos, cerca de 12 mil inserções e
15,9 mil remoções. Ficam misturados produto, refatorações, documentos,
experimentos e artefatos. Uma validação não identifica com segurança qual
candidato foi validado.

**Ação:** congelar uma cópia identificada; classificar os grupos; criar commits
pequenos por assunto; executar os gates no candidato exato e guardar fingerprint
e resultado. Não remover `prototypes/`, instalações F1 ou `.workbench-managed`
em massa: a documentação registra consumidores desses caminhos.

**Viabilidade:** alta para identificar e organizar; esforço alto para classificar
sem perder intenção ou evidência.

### 2. Grants de plugin não confinam processos no sistema operacional — P1

O gateway e a ordem `eligibility → authority/grants → approval → adapter` são
bons controles. A documentação de plugins, porém, afirma que stdio executa com
a identidade/acesso do usuário; o fingerprint cobre o manifest, não executável
ou dependências; o produto não oferece sandbox universal. A autorização
automática de tarefas de teste também avisa que não fornece sandbox.

É um limite declarado, não um bypass demonstrado. Um grant de `process` ou
`read` não deve ser entendido como confinamento. O OWASP inclui tool misuse,
abuso de identidade/privilégio, cadeia de suprimentos e execução inesperada
entre riscos agentic; o MCP oficial trata servidor local como software confiado
pelo usuário. Aplicar essa taxonomia ao protocolo stdio próprio é uma inferência.

Evidências locais: [garantias e limites dos plugins](../../plugins-locais.md) e
[contrato de segurança](../../agent/security.md).

**Ação:** antes do spawn, exibir comando, origem, fingerprint, diretório e
autoridade efetiva; adicionar cenário adversarial de acesso fora do workspace e
rede; avaliar depois um perfil isolado opcional (por exemplo, conta restrita ou
AppContainer no Windows). Até ter confinamento testado, chamar de execução de
plugin confiável, não de sandbox.

**Viabilidade:** transparência e cenários, alta; isolamento transversal,
investimento maior e capacidade independente.

### 3. Redaction não equivale a privacidade total — P1

Traces têm política versionada, limites e retenção; exports também passam por
redaction. A documentação de segurança declara que logs e alguns registros de
estado/reporting podem conter mais dados que eventos redigidos e que não há
garantia de redaction total.

Referência: [segurança CURRENT](../../agent/security.md) e
[inspector/exports](../../observability-inspector.md).

**Ação:** inventariar logs, memória, histórico, checkpoint, trace, relatório,
cache e bundle; registrar conteúdo, retention, local e limpeza; classificar
prompt, resposta, paths e dados do workspace. Criar testes de vazamento para
credenciais e conteúdo sensível em estruturas aninhadas. Considerar modo
“inspeção sem conteúdo” que mantenha IDs, status, duração e ferramentas.

**Viabilidade:** média; redaction das observações é centralizada, mas os demais
formatos têm owners diferentes.

### 4. Evidência live de modelo é histórica, não prova qualidade atual — P1

O documento CURRENT de avaliação registra um epoch histórico de 43 execuções:
14 passaram e 29 foram `MODEL_CAPABILITY`, usando uma configuração Qwen local
específica. O mesmo registro marca o epoch como diagnóstico/superseded e exige
novo epoch autorizado. Campanhas scripted e oráculos determinísticos testam o
harness, não provam que o modelo real atual use corretamente as capacidades.
Consulte a [avaliação CURRENT](../../agent/evaluation.md) e o [registro dos
modelos menores](../../weak-model-runtime.md).

**Ação:** quando existir endpoint identificado e autorização, rodar live-canary
limitado com fingerprint observado, custo/tempo, chamadas, efeitos e oráculos
independentes. Separar falha do harness da limitação do modelo. Não promover
`compact` ou alegar suporte a modelos menores com fixture scripted.

**Viabilidade:** alta no harness atual, condicionada a endpoint e autorização.

### 5. Ainda há mais de um lifecycle de interação — P1

Workbench WebView e `llm-agent local` usam `InteractiveSession`. O `llm-agent
chat` tradicional ainda passa por `interactive_session.run_chat`, com contexto,
controlador e recursos próprios. O Workbench Tk experimental mantém outra
`WorkbenchSession`, seu próprio `ThreadPoolExecutor` e caminho de sessão. Os
serviços da aplicação são compartilhados em níveis diferentes; estado
interativo, admissão, polling e apresentação não têm um único owner.

A escala aumenta o custo de entender mudanças: a árvore atual contém 1.001
arquivos Python sob `src/`; `quality/baseline.json` ainda registra 29 exceções
de módulos acima de 300 linhas e 18 exceções de complexidade. Isso não invalida
o ratchet existente, mas indica dívida concentrada que merece decomposição por
fronteira de domínio, sem quebrar módulos apenas para atingir contagem.

**Ação:** fazer matriz de paridade (workspace, LLM, cancelamento, pending,
approval, streaming, plugins e recuperação); escolher a jornada suportada;
migrar CLI chat por etapas e manter Tk explicitamente técnico até paridade ou
retirada. Evitar reescrita total.

Pontos de composição: [CLI chat](../../../src/llm_agent/interfaces/cli/interactive_session.py),
[sessão WebView](../../../src/llm_agent/interfaces/workbench/_webview_host.py),
[sessão compartilhada](../../../src/llm_agent/interactive/session.py) e
[Workbench Tk](../../../src/llm_agent/interfaces/workbench/session.py).

**Viabilidade:** média; já existe a fachada UI-neutral, mas compatibilidade exige
migração e regressão específicas.

### 6. Primeiro uso e descoberta ainda requerem conhecimento interno — P1

O propósito exige próximos passos descobríveis. O README tem 379 linhas e junta
começo rápido, instalação, produto instalado, chat, diretivas, continuidade,
histórico local e mapa do workspace. Os documentos agora têm índice (bom
avanço), mas ainda há muitas entradas. No modo local, `/commands` lista os
comandos da sessão; `/llm` é controle específico e não faz parte desse catálogo.
O WebView oferece paleta, então os caminhos diferem.

Entradas: [README](../../../README.md), [CLI local](../../../src/llm_agent/interfaces/cli/local_mode.py)
e [guia básico](../../guides/uso-basico.md).

**Ação:** primeiro uso de três passos: abrir projeto → tentar consulta local →
ativar LLM quando necessário. Oferecer tarefas por objetivo (“encontrar texto”,
“ver diff”, “preparar bateria”), indicando local/LLM/efeito. Medir tarefas
pequenas sem criar outro catálogo.

**Viabilidade:** alta; catálogo, paleta e guias já existem.

### 7. Falhas não viram facilmente avaliações reutilizáveis — P2

O projeto tem traces/replay, recibos, oráculos e filas, mas são ferramentas
separadas. Transformar falha observada em fixture, critério e verificador ainda
pode exigir cópia manual. A OpenAI recomenda usar traces para depurar e depois
datasets/runs repetíveis; a Anthropic destaca feedback real do ambiente e
ferramentas claras. Isso fundamenta a direção, não mede este Agent.

Fontes locais: [inspector](../../observability-inspector.md),
[fila de testes](../../guides/fila-de-testes.md) e
[avaliação](../../agent/evaluation.md).

**Ação:** permitir criar caso de avaliação de um run revisado, com workspace
sanitizado, objetivo, expectativa, oráculo independente e origem do trace;
exigir preview e aprovação humana. Começar com efeitos verificáveis, sem gerar
expected answers automaticamente.

**Viabilidade:** média; precisa de formato de dataset estável, privacidade e
versionamento de oráculos.

### 8. Bateria longa continua vinculada ao processo da interface — P2

A fila tem preparo, status, pausa, parada, retomada e verificação. A pesquisa
local registra que o runner iniciado pelo Workbench depende da janela/processo
permanecer aberto. A CLI mantém o processo no terminal; estado salvo não é um
worker vivo após fechar a interface.

Referência: [pesquisa da fila/CLI](cli-unificada-uso-local-20261102.md).

**Ação:** primeiro permitir deixar janela aberta/minimizada e voltar depois,
com recuperação clara. Só então estudar runner separado com protocolo local,
lock, lease, heartbeat, cancelamento, logs e retomada idempotente.

**Viabilidade:** média para execução desacompanhada com janela; baixa/média para
serviço independente.

### 9. O streaming pode aparecer em blocos — P2

O provider envia stream quando o perfil declara suporte; a bridge coleta deltas
e o JavaScript atualiza a mensagem. `wait_events` pode esperar até 1.000 ms e
bombeia a stream antes da espera, então um delta pode aguardar o próximo polling.

**Ação:** medir tempo até primeiro token/intervalo entre deltas. Se o atraso
importar, acordar a bridge ao receber delta ou reduzir o intervalo de forma
adaptativa; manter limites de fila, thread ownership e resposta final após
truncamento.

Implementação: [bridge](../../../src/llm_agent/interfaces/workbench/webview.py),
[polling/renderização](../../../src/llm_agent/interfaces/workbench/static/workbench.js)
e [stream do provider](../../../src/llm_agent/agent/llm/providers/openai_compatible.py).

**Viabilidade:** alta em princípio; precisa preservar backpressure e consumo de
CPU/memória.

### 10. Instalação e raiz podem ser mais reproduzíveis e menos ruidosas — P2

O `pyproject.toml` usa intervalos abertos enquanto locks servem CI/dev. Um novo
build pode resolver versões diferentes se o artefato não consumir um lock/pin
de release. A raiz também contém `.venv`, `build`, caches, instalações e
evidências. Várias pastas são ignoradas e algumas são necessárias a launchers e
experimentos, então remoção ampla seria arriscada.

Fontes: [pyproject](../../../pyproject.toml),
[baseline de complexidade](../../../quality/baseline.json) e
[guia de contribuição](../../../CONTRIBUTING.md).

**Ação:** registrar Python/dependências/wheel/hashes no manifesto de cada
instalação. Classificar pastas como fonte, estado necessário, cache descartável,
histórico ou experimento; criar inventário/cleanup com `--dry-run` e allowlist
que proteja F1, `.workbench-managed` e protótipos ativos.

**Viabilidade:** alta para inventário; alta/média para lock multiplataforma com
instalação offline.

## Aspectos que estão bem encaminhados

- `docs/proposito.md` define Workbench/Agent, uso sem LLM, verificação,
  transparência e modularidade.
- Authority, approval e execução estão separados; approval não concede
  authority.
- Fila e oráculos distinguem resposta textual de efeito observado; docs dizem
explicitamente que auto-approval não é sandbox.
- Há gates de complexidade, mypy, arquitetura, testes e pacote instalado fora do
  checkout.
- Traces têm retenção limitada e projections distintas dos dados canônicos.
- Extensions têm grants/escopo; mudança de manifest não concede capacidades
  novas automaticamente.

Essas propriedades devem ser mantidas ao executar as prioridades. Não há razão
para relaxar gates, acoplar o ScannerCore do TCC ao núcleo ou habilitar
automaticamente ferramentas escolhidas pelo modelo.

## Ordem de execução sugerida

1. Classificar o estado do checkout e fechar um candidato por assunto.
2. Definir claramente confiança e limites do processo de extensão; testar
   tentativas adversariais antes de escolher tecnologia de sandbox.
3. Fechar privacidade ponta a ponta com inventário e testes de redaction.
4. Atualizar avaliação de modelo real sem misturar métricas scripted.
5. Fazer matriz de paridade e unificar as jornadas suportadas por etapas.
6. Simplificar discovery e ligar runs revisados a casos reproduzíveis.
7. Planejar runner de filas, streaming, lock de release e limpeza com dry-run.

## Referências externas e interpretação

- [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/download/52117/): taxonomia para tool misuse, abuso de identidade, supply chain, execução inesperada e confiança humana. Aplicar os riscos ao protocolo deste projeto é inferência deste relatório.
- [OpenAI — Evaluate agent workflows](https://developers.openai.com/api/docs/guides/agent-evals) e [Trace grading](https://developers.openai.com/api/docs/guides/trace-grading): traces para depuração e datasets/runs para avaliações repetíveis; não é recomendação de adotar a plataforma OpenAI.
- [Anthropic — Building effective agents](https://www.anthropic.com/research/building-effective-agents): simplicidade, ferramentas claras e feedback do ambiente. Aplicação à experiência deste Agent é inferência.
- [MCP — Security](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/SECURITY.md): referência de que servidor local é software confiado pelo usuário; este projeto usa protocolo stdio próprio, não MCP.
- [OpenAI — Sandbox Agents](https://developers.openai.com/api/docs/guides/agents/sandboxes): referência de separar controle confiável do ambiente de execução. A viabilidade local de sandbox não foi comprovada aqui.

## Limites

Auditoria somente leitura. Não executei testes, quality gates, campanhas, rede
ou modelos. Os números Git são o estado observado em 02/11/2026; não identificam
autoria nem provam que cada arquivo deva permanecer. Acessibilidade, proteção
real do processo e qualidade live exigem ensaios específicos antes de serem
declaradas como falhas ou aprovadas.
