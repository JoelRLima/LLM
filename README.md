# LLM Agent

LLM Agent é um agente local de desenvolvimento, instalável por Python, com CLI,
planning linear/hierárquico, leitura e busca no workspace, workflows de mudança
com validação e execução multitarefa local. Um único runtime coordena as tarefas;
o projeto não é uma plataforma distribuída de multiagentes.

## O que está disponível

- leitura e busca confinadas ao workspace;
- alterações suportadas por `code_task` → `ChangeSet` → `ProjectValidator`;
- Shell/Git/Ruff em superfície reduzida e allowlisted, sem shell arbitrário;
- memória persistente por workspace; busca semântica é opcional;
- extensions stdio 1.0 condicionadas a catálogo, configuração e authority
  explícita da tarefa;
- web search para a persona apropriada, sujeita a política/aprovação de rede.

`file_writer` ainda existe para consumidores low-level/admin, mas não é
model-actionable. MCP, sandbox universal de sistema operacional e instalação de
pacotes pelo modelo não são fornecidos. Discovery ou aprovação não concedem
authority. A [matriz técnica completa](docs/README.md#matriz-current-de-capabilities)
explica cada boundary.

## Instalação rápida

Requer Python 3.10+ e um endpoint OpenAI-compatible configurado.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
llm-agent config init
llm-agent config path
llm-agent doctor
```

Edite o profile indicado por `config path` com endpoint e modelo. Em Linux ou
macOS, ative o ambiente com `source .venv/bin/activate`. A instalação core não
inclui a stack opcional de memória semântica; use `.[ml]` quando necessário.

## Uso

```powershell
llm-agent chat
llm-agent run --workspace C:\caminho\projeto "Analise este repositório"
llm-agent run --workspace C:\caminho\projeto --json "Resuma o projeto"
llm-agent run --workspace C:\caminho\projeto --yes "Aplique a alteração"
```

`run` é headless e nunca lê `stdin`. Se uma ação exigir consentimento e `--yes`
não estiver presente, ela termina bloqueada sem executar o efeito. `--yes`
fornece aprovação para aquela execução; não cria capability, grant ou authority
e não transforma validação ausente em sucesso.

`--home DIR` fornece uma raiz portátil para configuração e estado; sem override,
paths são resolvidos nos diretórios de usuário do sistema. O pacote instalado
não é usado como diretório gravável.

## Estado

Marcos 1 e 2 estão fechados. No Marco 3, o Block A de avaliação está **GREEN
LOCAL**; Blocks B e C não foram concluídos e a Standalone V1 ainda não foi
declarada. Portanto, testes determinísticos atuais não devem ser interpretados
como benchmark de modelo real ou gate final de release.

## Documentação e contribuição

O [índice técnico authoritative](docs/README.md) mapeia arquitetura, contratos
CURRENT, ADRs, referências e registros históricos. Consulte também
[CONTRIBUTING.md](CONTRIBUTING.md) para qualidade e contribuição.
