# Workbench Desktop — avaliação tecnológica e proposta visual

Data: 21/10/2026  
Branch: `feat/agent-workbench`  
Escopo: experimento offline, sem endpoint real, sem migração da interface de produção.

## Diagnóstico

A janela Tk existente prova a fatia funcional e preserva a camada `application`, mas ainda não oferece uma hierarquia visual adequada para um workbench técnico. Ela usa uma coluna fixa de conversa e aloca o painel direito mesmo vazio; navegação, inventário de resultados e composição do chat disputam espaço com controles de formulário nativos. O tema e os componentes têm pouca margem para expressar Markdown, estados de execução e arquivos técnicos com consistência.

O caminho de dados está bem separado e deve permanecer. A UI chama `WorkbenchSession`, que usa `TaskExecutionStart`, `execute_submission`, `TaskReportBuilder` e `WorkbenchArtifacts`; report e outputs conservam identidade, origem, completude e leitura limitada. Plugins atuais contribuem ferramentas de execução via contratos existentes. A UI só deve consultar essas projeções. Um renderizador visual é outro papel e não deve se tornar um segundo catálogo de plugins.

## Experimento reproduzível

Os candidatos ficam em [`prototypes/workbench-comparison`](../../../prototypes/workbench-comparison). Ambos reutilizam `WorkbenchSession` e `AgentApplication`, criam o mesmo workspace temporário com `notes.txt` e substituem somente o gateway de modelo pela fixture determinística já usada em `tests/integration/test_agent_application_evaluation.py`. O fluxo lê o arquivo com `file_reader`, produz o relatório pelo `TaskReportBuilder`, oferece a referência com `run_id` e completude, e lê o conteúdo pela camada Workbench. Nenhum endpoint remoto foi chamado.

- `tk_candidate.py` abre a janela Tk atual e aceita `--demo` para executar o fluxo offline ao abrir.
- `pywebview_candidate.py` usa `pywebview 6.2.1`, HTML/CSS/JavaScript sem framework e um bridge pequeno. O bridge aceita texto limitado, publica eventos e só lê um relatório cujo token veio de uma referência oferecida pela própria sessão. Caminhos, comandos, callbacks de extensões e JavaScript fornecido por extensões não são expostos.
- `pywebview/` implementa a proposta de navegação compacta, chat central, inspector que só aparece ao abrir o relatório, divisor redimensionável, completude recebida da aplicação, barra de estado, tema claro/escuro e breakpoints para janelas menores. O conteúdo do relatório entra com `textContent`; a página define CSP local sem scripts externos.

Executar Tk:

```powershell
.venv\Scripts\pythonw.exe prototypes\workbench-comparison\tk_candidate.py --demo
```

Executar pywebview após a instalação isolada usada neste teste:

```powershell
.venv\Scripts\python.exe -m pip install --target .tmp\workbench-pywebview-deps pywebview
$env:PYTHONPATH = (Resolve-Path .tmp\workbench-pywebview-deps).Path
.venv\Scripts\pythonw.exe prototypes\workbench-comparison\pywebview_candidate.py --demo
```

O `--demo` mostra a solicitação offline fixa, o resultado e a oferta do relatório. Abrir a oferta é uma ação explícita e preserva a conversa. No protótipo web, o painel pode ser fechado sem fechar a sessão. As vistas de artefato continuam pertencendo aos serviços existentes.

## Medições neste Windows

Python 3.12.10 x64, Windows 11, WebView2 Runtime `155.0.4283.62`, gateway offline fixo e mesma máquina. O working set soma os processos da janela iniciados durante a amostra (Python, processos auxiliares e `msedgewebview2`). CPU é o delta de tempo de CPU do conjunto em 10 segundos após o relatório ficar pronto; o percentual entre parênteses é relativo a um núcleo lógico. São medições pontuais, sem repetição estatística; working set e inicialização variam com cache e temperatura do runtime.

| Candidato | Até a UI utilizável | Até o relatório final | Working set em repouso após o fluxo | CPU em 10 s em repouso |
| --- | ---: | ---: | ---: | ---: |
| Tk/ttk atual | 0,79 s até o loop Tk ocioso | 1,60 s | 82,2 MB | 0,094 s (0,94%) |
| pywebview + WebView2 | 5,98 s até a ponte da página responder | 6,36 s | 492,7 MB | 0,281 s (2,81%) |

A amostra sem relatório ficou em 80,2 MB / 0,031 s por 10 s para Tk e 486,1 MB / 0,297 s por 10 s para pywebview. O runtime de execução é criado de forma preguiçosa: as diferenças de working set após o fluxo (cerca de 2 MB no Tk e 7 MB no pywebview) sugerem que a maior diferença é do wrapper WebView2, do motor Chromium e seus processos, e não do Agent. Esta é uma inferência a partir das amostras, não uma medição isolada do processo interno do Agent.

O protótipo web executou a solicitação e produziu o relatório final. O `run_id` da oferta coincidiu com o relatório original e com a observação de execução; a origem foi `TaskReportBuilder`. O bridge também recusou uma referência não oferecida e retornou o conteúdo original limitado pela aplicação. A checagem direta final da ponte reportou 8.025 caracteres para esta fixture. O log local de inicialização (`prototypes/workbench-comparison/pywebview-demo.err.log`, evidência histórica não distribuída com o candidato) registra exceções COM: o controlador WebView2 é acessado fora da thread de UI e a chamada falha em `QueryInterface`. Definir `sys.coinit_flags = 2` (STA) não eliminou o erro. A ponte e o evento `pywebviewready` funcionaram nesta execução, mas a confiabilidade do host visual ainda não está demonstrada.

Não há capturas anexadas: o ambiente abriu as janelas, mas a integração de Computer Use disponível neste turno retornou inventário vazio e não expôs `cua.getApp`/captura de janela (a chamada documentada falhou como método indisponível). Portanto, não marquei inspeção visual, redimensionamento manual, foco/teclado ou leitura de tela como verificados. O CSS inclui breakpoints e o Tk já tem teste de widgets, mas isso não substitui revisão humana da janela real.

## Comparação e decisão

| Tecnologia | Avaliação para este Agent |
| --- | --- |
| Tk/ttk | Menor custo medido e integração direta com Python. Mantém uma superfície de dependências enxuta. Tema, layouts técnicos ricos, Markdown e painéis responsivos exigem componentes e desenho próprios; a janela atual ocupa espaço lateral sem necessidade. É a opção estável de produção até um substituto passar suas verificações. |
| pywebview/WebView2 | A melhor correspondência ao design proposto: CSS torna simples compor navegação, conversa e inspector sob demanda; o bridge conversa diretamente com a aplicação Python, sem servidor de API separado nem framework JavaScript obrigatório. O custo medido foi aproximadamente 410 MB acima do Tk e o host WinForms/COM apresentou exceções no ambiente desta avaliação. Precisa de bridge restrito, CSP, prova de encerramento/cancelamento e validação em Windows antes de adoção. A documentação do projeto lista WebView2 Runtime e `pythonnet` como dependências Windows, descreve o bridge JS↔Python e recomenda atenção a CSRF quando se escolhe API HTTP local ([instalação](https://github.com/r0x0r/pywebview/blob/master/docs/guide/installation.md), [arquitetura](https://pywebview.idepy.com/en/guide/architecture), [segurança](https://pywebview.idepy.com/en/guide/security)). |
| PySide6 | Alternativa desktop coerente se o requisito de memória ou confiabilidade do host web pesar mais que a flexibilidade CSS. Não está instalado aqui e não foi medido. Widgets Qt cobrem splitter, navegação e editores; para HTML rico pode-se acabar incorporando Qt WebEngine/Chromium. O empacotamento oficial `pyside6-deploy` envolve Nuitka, e o material Qt separa as condições de licença do WebEngine/Chromium ([deployment](https://doc.qt.io/qtforpython-6/deployment/index.html), [licenciamento do WebEngine](https://doc.qt.io/qtforpython-6/overviews/qtwebengine-licensing.html)). |
| Tauri | Possível se o produto precisar de uma camada frontend Rust/web independente ou de controles de processo mais explícitos. Para preservar a aplicação Python, é necessário desenhar o IPC e ciclo de vida do sidecar, adicionar Rust/build tools e, se houver um framework web, Node. No ambiente faltam Rust/Cargo e Node, então não foi construído nem medido. Os requisitos Windows incluem Microsoft C++ Build Tools e WebView2 ([prerequisitos Tauri](https://v2.tauri.app/start/prerequisites/)). |

**Recomendação de produto:** a avaliação visual do usuário favoreceu claramente o protótipo pywebview/WebView2. WebView2 passa a ser a tecnologia preferencial para o próximo piloto funcional do Workbench, preservando a estética atual do protótipo e sua bridge restrita. Tk permanece a referência e alternativa de contingência; esta preferência não decide uma migração definitiva nem descarta o frontend Tk. O histórico de erros COM e o custo de memória ainda exigem validação do ciclo de vida, benchmark e revisão visual antes de qualquer adoção além do piloto. Não incluir React/Node por padrão. Se a confiabilidade COM não for demonstrada após os testes de encerramento, comparar PySide6 antes de ampliar a dependência do webview.

## Design e arquitetura a manter

Usar tokens pequenos para superfície, borda, texto, semântica e acento; escala de espaçamento e tipografia comum; navegação recolhível; chat como região maior; composer preso à base; status factual; inspector à direita somente com artefato aberto. Em 1280×800, deixar o chat ocupar a maior parte da janela. Em 1024×700, compactar a navegação e limitar a largura do inspector. Abaixo do mínimo, transformar o inspector em sobreposição recolhível. Abas carregam sob demanda e mantêm a chave `(workspace_id, run_id, artifact_id)`.

O bridge de produção deve aceitar somente contratos de aplicação tipados. Mensagem ou artefato não vira HTML executável. Uma referência inválida continua indisponível; a interface mantém os estados `partial`, `final` e `unknown` tal como recebidos. A oferta de visualizadores será um contrato declarativo validado para renderizadores nativos conhecidos. Extensões de execução preservam manifests, grants e capabilities atuais e não fornecem código privilegiado à página.

## Plano incremental

1. Reproduzir e corrigir a chamada COM em um exemplo pywebview mínimo, incluindo STA, loop principal, versões de Python/pythonnet/WebView2, ausência de recursão no wrapper e encerramento. Medir em pelo menos dois Windows limpos e repetir a amostra de recursos.
2. Fazer revisão humana do protótipo em 1280×800, 1024×700 e no tamanho mínimo; testar teclado, foco, resize, fechamentos, Unicode, strings longas e conteúdo hostil. Capturar as janelas nessa etapa.
3. Manter `application/workbench` como única fonte. Isolar os métodos visíveis de sessão numa fachada declarativa com listas de operações permitidas; testar referência inválida, cancelamento, fechamento durante tarefa, saída truncada e relatório parcial/desconhecido.
4. Substituir a apresentação em uma branch de implementação separada, migrando primeiro chat→relatório lateral. Depois migrar outputs, arquivos, diffs, logs, testes e inventário, um visualizador nativo por vez.
5. Empacotar para instalação Windows limpa sem depender do Python de desenvolvimento. Verificar WebView2 Evergreen, versão do runtime, atualização, política de cache privada, assinatura/distribuição e tamanho do instalador. Comparar com um pacote PySide6 antes de decidir o suporte de longo prazo.
6. Só tornar o frontend web padrão quando os testes de aplicação, UI, segurança, acessibilidade básica, empacotamento e revisão visual passarem; deixar fallback Tk disponível durante a primeira distribuição.

## Validação e limites

- `tests/integration/test_workbench_vertical_slice.py`: **3 passed** em 5,77 s no Windows; a execução verificou Agent, ferramenta, relatório e widgets Tk. Os gates de branch previamente relatados permanecem em [`workbench-validation-20261021.md`](../../history/2026/milestones/workbench-validation-20261021.md).
- Ruff dos protótipos: **aprovado**. A compilação em memória de cada arquivo Python: **aprovada**.
- Bridge pywebview: análise offline concluída, completude `final`, origem e `run_id` preservados; leitura de referência inválida recusada.
- Nenhuma mudança foi feita em critérios ou aceitação W23. Nenhum commit, merge, push ou chamada de modelo real foi feito.
- Não foram medidos PySide6/Tauri, pacote instalado ou uso em máquinas sem WebView2. O trabalho não substitui aprovação visual humana.
