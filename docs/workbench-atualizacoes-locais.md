# Instalação e atualizações locais do Workbench

Este fluxo instala o Workbench deste checkout no Windows sem baixar dependências. É separado do instalador W18 distribuído em ZIP; o PATH do usuário não é alterado.

Abra `Workbench.cmd` e escolha **Gerenciar instalação**. No PowerShell:

```powershell
Set-Location 'D:\LLM Agent Harness'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Gerenciar_Workbench.ps1 -Operation Install
.\Workbench.cmd open
```

O launcher principal detecta a instalação gerenciada e encaminha a abertura; os detalhes do wrapper interno não são necessários para o uso normal.

O gerenciador usa o Python de `.venv` para gerar um wheel com o backend definido em `pyproject.toml`. Ele copia as dependências desktop de um runtime local existente e instala o wheel offline em uma pasta nova. Sem dependências ou ferramentas de build locais, a operação falha com diagnóstico; nenhum download é iniciado. Esta instalação continua dependendo do Python base desta máquina.

O launcher novo consulta `.workbench-managed/current.json` e abre o pacote instalado. A instalação confere o caminho dos imports, os arquivos do pacote e `pip check` antes de promover o candidato. Falhas mantêm o apontamento anterior. O F1, seus dados e seu launcher original são preservados.

Na primeira abertura, o gerenciador migra uma cópia do `config.json` do projeto para a configuração persistente da instalação. O arquivo original não é alterado, e aberturas posteriores preservam a configuração já existente. Documentos inválidos falham na validação normal; as regras não são relaxadas.

## Atualizar pela interface

Ao abrir, voltar o foco para a janela e a cada minuto, o botão **Atualizações** verifica o conteúdo local. Mudanças no código, recursos da interface, configuração de empacotamento e gerenciador são detectadas por SHA-256, mesmo sem mudar o número da versão. Cache Python e arquivos do workspace não disparam atualização.

Quando aparecer **Atualização disponível**, abra o painel e escolha **Instalar atualização**. O aplicativo prepara outra instalação e mantém a janela atual. Depois, **Reiniciar agora** abre o candidato promovido preservando o workspace ativo. A operação não consulta o GitHub nem chama o modelo. O código precisa estar salvo no checkout para entrar no pacote.

Instalação e reinício são recusados enquanto existe uma tarefa ativa. Uma nova tarefa também não é admitida pela bridge enquanto ela instala a atualização. Fechar o painel não cancela o preparo em andamento; os logs ficam no candidato.

## Recuperar e desinstalar

```powershell
.\Gerenciar_Workbench.ps1 -Operation Status
.\Gerenciar_Workbench.ps1 -Operation Update
.\Gerenciar_Workbench.ps1 -Operation Rollback
.\Gerenciar_Workbench.ps1 -Operation Uninstall
```

`Rollback` verifica os arquivos da versão anterior antes de trocar o apontamento; reinicie a janela já aberta. `Uninstall` desativa esta instalação gerenciada retirando seu apontamento ativo. Ele preserva candidatos, logs, histórico, configurações e workspaces para recuperação e reinstalação. Não faz limpeza física nem remove a instalação F1/W18.

Os candidatos ficam em `.workbench-managed/versions/`, os logs de abertura em `.workbench-managed/logs/` e a configuração persistente em `.workbench-managed/data/`. Cada candidato contém `installation.json`, seu wheel e `maintenance.log`. Investigue falhas nesse log; a versão atual continua disponível quando o build ou a validação falham.
