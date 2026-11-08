# Uso local e ativação da LLM

`llm-agent`, `llm-agent chat` e `llm-agent local` usam agora essa mesma sessão
compartilhada, com LLM inicialmente desativada. A sessão antiga permanece em
`llm-agent chat --legacy-session` para compatibilidade. Consulte a
[matriz de paridade](paridade-app-cli.md).

O Workbench abre em **Uso local**, mesmo quando já existe um modelo configurado. O botão **Ativar LLM**, no topo, libera conversa e tarefas com IA. O botão passa a **Desativar LLM** para voltar ao uso local. A troca vale para a sessão atual; reabrir o app volta ao uso local.

Na CLI, `llm-agent local` abre a mesma sessão compartilhada no terminal e também começa com a LLM desativada. Use `/llm on` para liberar o modelo, `/llm off` para voltar ao modo local ou `/llm test` para consultar explicitamente o catálogo `/models` do servidor. O teste não envia geração; `/llm status` também não faz rede. Ativar não envia texto; se um pedido foi recusado porque a LLM estava desligada, reenvie-o depois de ativar. Sem `--workspace`, a sessão usa a pasta atual.

Ativar não consulta o provedor nem gera texto. É necessário haver um perfil válido; **Escolher perfil** na Visão geral ou o botão de ativação abre a seleção entre os perfis configurados. Se a lista estiver vazia, use **Configurar perfil** nessa tela para salvar um endpoint OpenAI-compatible; o formulário não salva chave de API. Para servidores remotos, configure as credenciais separadamente. Ativação não comprova conexão ou prontidão de geração. O diagnóstico do catálogo continua disponível em **Sessão**, como ação explícita.

## O que funciona localmente

| Função | Geração por LLM | Configuração de modelo |
| --- | --- | --- |
| Listar, ler e buscar arquivos; Git status/diff | Não | Dispensável |
| Executar um comando local pontual (`/exec`) | Não | Dispensável |
| Escolher/trocar projeto e consultar ajuda/estado | Não | Dispensável |
| Analisar código; `/code analyze` e `/code review` | Não: análise estática determinística | Dispensável |
| Preparar/abrir baterias, conferir estrutura, ler evidências e exportar HTML | Não | Dispensável |
| Inspecionar execuções retidas e consultar catálogo de plugins | Não | Dispensável |
| Histórico, memória, prompt e controles do runtime | Não geram texto | Precisam de configuração válida para inicializar o runtime |
| Conversar, gerar/modificar código e executar tarefas com IA | Sim | Perfil configurado e LLM ativada no Workbench |

Em **Arquivos**, abra um arquivo e use **Analisar código**. A análise reutiliza o mesmo motor canônico da CLI, preserva o arquivo e não desloca o rascunho da conversa. É análise estática, com as limitações desse motor; não equivale a uma avaliação semântica feita por IA.

No modo local, a entrada aceita comandos do shell do sistema ou ações como
`/ls` e `/find`. `/exec comando` funciona também com a LLM ativada e deixa claro
que só o comando escrito será executado. No Workbench, o Windows pede confirmação
e mostra o comando e a pasta. A execução usa sua conta, sem sandbox, parte do
workspace ativo e começa um processo novo a cada envio; `cd` não altera envios
posteriores. Use `/workspace switch` para trocar a pasta inicial. Na CLI, digite
`/exec` seguido do comando ou envie o próprio comando durante o uso local.
`/exec --timeout 60 COMANDO` define o limite em segundos (padrão 300, máximo
3600); a saída é limitada. No Workbench, comandos acima de 3000 caracteres são
recusados para que a confirmação mostre integralmente o comando informado.

No painel **Testes**, preparação e consulta permanecem disponíveis no uso local. O atalho **Ativar LLM para executar** usa o mesmo controle do topo. Executar uma bateria ainda exige conferir a preparação e autorizar as chamadas naquela execução. Ativação não concede aprovação automática a ferramentas nem muda permissões do projeto.

## Contrato e limites

A autorização fica em `InteractiveSession`, com `set_llm_enabled(bool)`, `llm_enabled` e bloqueio antes do caminho de modelo. A bridge só delega e apresenta a projeção. Mudanças são recusadas durante uma operação ou após encerramento; o botão também confere a identidade do projeto. Pendências e resultados não são apagados ao desativar.

A composição desktop, `llm-agent local`, `llm-agent` e `llm-agent chat` iniciam a mesma `InteractiveSession` com a LLM desativada. Ative-a com `/llm on` na CLI ou pelo botão no Workbench. `llm-agent chat --legacy-session` mantém o fluxo antigo somente para compatibilidade. Os serviços de análise, consultas, comandos locais e baterias são compartilhados; cada interface só adapta entrada e apresentação.

“Uso local” significa que a geração por LLM está desativada neste fluxo. Não é sandbox nem bloqueio global de rede: consultas explícitas ao catálogo, plugins e outros serviços preservam seus próprios contratos.

## Verificação

Os testes e relatórios em `reports/without-llm-20261102/` cobrem uma revisão anterior
da sessão local; não cobrem o executor de comandos `/exec`, a configuração de perfil
no Workbench nem o criador de casos desta revisão. A execução de comandos é uma
ação explícita da pessoa, roda sob sua conta e não possui sandbox. A suíte de
regressão não foi executada após essas mudanças. Nenhuma LLM real foi chamada.


## Diagnóstico do servidor de modelos

O Workbench oferece **Verificar servidor agora** em **Sessão**. A ação consulta `/models` em um worker com timeout limitado; a interface continua respondendo e nenhum texto é gerado. O resumo junto a **Ativar LLM** informa se o catálogo respondeu. “Servidor respondeu; geração não testada” diferencia a resposta dessa consulta da prontidão para inferência.

Pesquisei os padrões oficiais. O llama.cpp documenta `GET /health`: HTTP 200 significa que o modelo carregou, e 503 que ainda está carregando; também oferece `/v1/models`. [Documentação do servidor llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md). Ollama documenta `GET /api/version` como checagem de saúde na interface e `GET /api/tags` para listar modelos locais; a API compatível com OpenAI usa outro caminho. [Introdução à API do Ollama](https://docs.ollama.com/api/introduction), [API do Ollama](https://docs.ollama.com/api). A API OpenAI oferece listagem de modelos; ela verifica que essa rota respondeu, sem provar que uma geração terá êxito. [Listar modelos na API OpenAI](https://developers.openai.com/api/reference/resources/models/methods/list).

Como o Agent aceita endpoints compatíveis com OpenAI de vários provedores, não há uma rota de saúde única. A checagem é explícita, sob demanda, para evitar requisições inesperadas a serviços remotos ou assumir uma rota específica. Ela é limitada a cinco segundos no worker. Se surgirem integrações nativas com outros provedores, uma checagem específica por provedor pode distinguir melhor “servidor ativo” de “modelo pronto”, ainda sem alegar que uma geração foi testada.
