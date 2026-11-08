# Workbench — LLM Agent

Ambiente local para trabalhar em projetos pelo app ou pelo terminal. Você pode
navegar em arquivos, buscar texto, consultar Git, executar comandos locais
pontuais, fazer análises estáticas, gerenciar plugins e preparar baterias de
testes sem ativar uma LLM.

## Começar

1. No Windows, execute `Workbench.cmd` e escolha abrir o app. Para abrir direto,
   use `Workbench.cmd open`.
2. Clique em **Escolher projeto**. Comece por arquivos, busca, análises ou Testes.
3. Quando precisar de IA, escolha um perfil e clique em **Ativar LLM**. Ativar
   libera o uso do modelo; envie seu pedido em seguida. **Testar conexão** consulta
   o catálogo do servidor e não comprova que a geração funciona.

O mesmo núcleo atende app e CLI. Ambos começam em modo local. O Agent é uma
das funções desse ambiente; operações locais continuam disponíveis com a LLM
desativada.

## Usar pelo terminal

Depois de instalar, abra uma sessão:

```powershell
llm-agent local --workspace "C:\projetos\app"
```

Na sessão:

| Objetivo | Comando | Usa LLM? |
| --- | --- | --- |
| Ver arquivos | `/ls` | Não |
| Encontrar texto | `/find texto` | Não |
| Ver alterações | `/git diff` | Não |
| Descobrir recursos | `/commands` | Não |
| Conferir estado da LLM | `/llm status` | Não |
| Executar comando local | `/exec Get-ChildItem` ou comando direto no modo local | Não; executa sob sua conta, sem sandbox |
| Consultar servidor | `/llm test` | Não gera texto; consulta `/models` |
| Ativar ou desativar IA | `/llm on` ou `/llm off` | Não envia tarefa |
| Acompanhar ou cancelar | `/status` ou `/cancel` | Não |

`llm-agent chat` e `llm-agent local` usam essa mesma sessão. Sem subcomando,
`llm-agent` preserva a configuração guiada e a escolha de workspace da W17.
Durante uma tarefa na sessão compartilhada, você
pode continuar consultando estado e decidir aprovações. Novos pedidos ficam
pendentes para envio explícito com `/pending send ID`.

Cada comando local começa em um processo novo na pasta do projeto. O diretório
alterado por `cd` não persiste entre envios; use `/workspace switch` para trocar
o projeto. No Workbench, o Windows mostra uma confirmação antes de executar.

## Instalar para desenvolvimento

Requer Python 3.10+; o app WebView2 requer Windows e WebView2 Runtime.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
llm-agent doctor
```

Para usar a janela desktop no Windows, instale também o extra `desktop`:
`python -m pip install -e ".[dev,desktop]"`.

Para configurar IA na CLI, execute `llm-agent config init`, consulte
`llm-agent config path` e edite endpoint/modelo no arquivo indicado. No app,
use **Sessão → Perfil e modelo → Configurar perfil** para salvar endpoint e
identificador do modelo; credenciais de provedores remotos continuam sendo
configuradas separadamente. A ativação da LLM é explícita. Chamadas ao modelo
podem ter custo.

## Testes que podem ficar rodando

Use **Testes** no app ou `llm-agent queue`. A bateria guarda estado e verifica
efeitos por critérios externos; a resposta do modelo sozinha não comprova que
uma tarefa foi concluída. A opção **Continuar em processo independente** permite
fechar o app; na CLI, use `queue run ... --live --detach`. Depois, consulte
`queue status`, `queue pause` ou `queue stop`.

## Permissões e transparência

Efeitos passam pelos controles de autoridade e aprovação do núcleo. Plugins
stdio autorizados executam como processos confiáveis do usuário, com acesso do
sistema operacional; grants não fornecem sandbox. O gerenciador mostra comando,
diretório, fingerprint e permissões antes da autorização.

Logs e exports redigem credenciais conhecidas. Histórico, memória, resultados e
arquivos de projeto podem guardar conteúdo de trabalho; consulte o inventário
de privacidade antes de compartilhar dados.

## Documentação

- [Guia básico](docs/guides/uso-basico.md) e [uso local/LLM](docs/guides/uso-local-e-llm.md)
- [Baterias e verificação](docs/guides/fila-de-testes.md)
- [Referência expandida de operação e compatibilidade](docs/guides/referencia-operacional.md)
- [Propósito](docs/proposito.md), [arquitetura e documentação completa](docs/README.md)
- [Dados locais, retenção e privacidade](docs/guides/privacidade-dados-locais.md)
- [Contribuir e validar](CONTRIBUTING.md)
