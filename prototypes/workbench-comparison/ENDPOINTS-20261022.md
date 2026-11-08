# Demo WebView2 com o endpoint configurado no CLI

O modo padrão deixou de usar `JourneyGateway`. A composição agora cria `WorkbenchSession(TaskExecutionStart(...))`, com os mesmos campos de inicialização do CLI chat. Ao submeter a mensagem, a sessão chama o `start_interactive_session` canônico; o runtime resolve configuração/perfil e cria o adaptador HTTP existente. Não há cliente HTTP novo, endpoint fixo, injeção de gateway ou substituição global de startup. A fixture compartilhada usada pelo Tk não foi editada.

## Execução

Feche a janela anterior do protótipo antes de iniciar a versão atualizada. Na raiz do repositório, como usuário comum:

```powershell
.venv/Scripts/python.exe prototypes/workbench-comparison/run_webview_inspection.py --demo
```

Esse comando usa a descoberta de home/configuração do CLI, o perfil padrão existente e o diretório atual como workspace. Não inicializa uma configuração de teste nem cria um workspace temporário. `--demo` envia uma única mensagem de apresentação por padrão; sem `--demo`, abre a janela e aguarda uma mensagem manual.

Para escolher explicitamente os mesmos parâmetros usados no CLI:

```powershell
.venv/Scripts/python.exe prototypes/workbench-comparison/run_webview_inspection.py --demo --workspace "D:\seu-workspace" --home "D:\seu-home-do-agent" --config "D:\sua-config.json" --profile "seu-perfil-existente" --demo-prompt "Sua solicitação de demonstração"
```

Os caminhos e o nome acima são exemplos; utilize os valores reais do seu comando CLI. Flags omitidas mantêm a resolução canônica. Credenciais e variáveis de ambiente continuam sendo resolvidas pelos componentes já usados pelo CLI, sem copiá-las para o HTML. O diretório atual é preservado pelo lançador. Pode-se executar `pywebview_candidate.py` diretamente com as mesmas flags, mas o lançador também registra host, subprocessos e stderr.

O protótipo usa a sessão interativa em **READ_ONLY**. Aprovações que exigiriam interação continuam indisponíveis nesta fatia; ele não passa a conceder operações por usar um endpoint real. A aplicação mantém o lock canônico por workspace: um CLI já ativo no mesmo home/workspace pode impedir uma segunda sessão.

## Disponibilidade e fixture

A interface informa **Endpoint do CLI · não verificado**. Isso significa seleção do caminho configurado, não confirmação de saúde do servidor. A criação da janela não faz geração nem sonda endpoints. Configuração inexistente/inválida ou endpoint inacessível produzem o erro/settlement canônico quando a solicitação é enviada; não há fallback silencioso para respostas simuladas. A demo só agenda outra rodada após `succeeded`, preservando a interrupção em falha, bloqueio, cancelamento ou erro. `--demo-rounds` permanece disponível na entrada direta do candidato para campanhas explicitamente solicitadas.

O modo determinístico exige opção explícita:

```powershell
.venv/Scripts/python.exe prototypes/workbench-comparison/run_webview_inspection.py --offline-fixture --demo
```

Nesse caso a interface informa **Fixture offline · sem modelo real**. A entrada do candidato rejeita a combinação da fixture com `--home`, `--config`, `--profile` ou `--workspace`, em vez de ignorar a seleção do usuário. Os documentos anteriores descrevem o comportamento histórico; este documento substitui suas instruções de lançamento para o modo padrão.

## Validação

- **4 testes novos Python passaram:** opções e composição padrão; isolamento da fixture; igualdade do `TaskExecutionStart` com o construído pelo CLI, sem startup antecipado; submissão real pelo runtime até o adaptador HTTP, usando perfil/configuração isolados e conexão simuladamente indisponível. Esse último teste confirma URL/modelo selecionados, settlement sem sucesso e capacidades READ_ONLY. Só a fronteira HTTP é substituída; outras requisições HTTP são proibidas no teste.
- **5 testes anteriores de bridge/fixture**, **17 cenários de submissão/disponibilidade**, **12 cenários de frontend** e auditoria dos **18 botões**, incluindo a oferta dinâmica, passaram. O teste de botões também confere a apresentação dos modos configurado e offline.
- Ruff e compilação dos arquivos Python alterados passaram. Os arquivos JavaScript foram executados pelo runtime Node de `node_repl`.

```powershell
.venv/Scripts/python.exe prototypes/workbench-comparison/test_webview_endpoints.py
.venv/Scripts/python.exe prototypes/workbench-comparison/test_webview_bridge.py
node prototypes/workbench-comparison/test_webview_submission.mjs
node prototypes/workbench-comparison/test_webview_ui.mjs
node prototypes/workbench-comparison/test_webview_buttons.mjs
```

Os registros novos estão em `evidence/endpoints-20261022/`. Os testes criam somente configuração/estado isolados nessa pasta e não leem nem alteram o perfil real do usuário. Não houve chamada de geração ao endpoint real, inicialização de servidor/modelo, carregamento de Qwen ou aceite visual nesta rodada. Assim, o roteamento está testado, mas a disponibilidade e a resposta do endpoint real ainda precisam ser conferidas na execução manual.

Tk, application, contratos e evidências anteriores permanecem preservados. Não houve alteração de permissões, commit, merge ou publicação. O pacote incremental deve ser extraído na raiz do mesmo repositório.
