# Plataforma standalone

> **STATUS: CURRENT — PRODUCT/PLATFORM REFERENCE.** A visão original está no
> [ADR 0002](adr/0002-visao-do-assistente-standalone.md); operação concreta em
> [operacao-standalone.md](operacao-standalone.md).

## Escopo entregue

O pacote instalado separa aplicação, workspace e estado; compõe um único
`AgentApplication` para chat/headless; oferece planning, tools builtin,
extensions stdio condicionais, memória persistente, diagnóstico e reporting.
Paths, config versionada e migração são explícitos.

```text
interface → AgentApplication → planning/orchestration
→ ToolInvocationGateway → builtin ou extension stdio
→ result/measurement/state → final response
```

O núcleo aplica policy; adapters traduzem bordas. Uma interface, provider,
persona ou extension não pode criar caminho alternativo model-actionable.

## Jornadas CURRENT

- **fora do checkout**: configuração e estado usam `AppPaths`/`WorkspacePaths`,
  nunca o pacote como diretório gravável;
- **análise read-only**: view e gateway limitam as tools e o workspace não é
  mutado;
- **alteração**: `code_task` produz `ChangeSet`, aplica policy/aprovação e
  valida ou faz rollback;
- **negação**: capability/authority ausente encerra antes do efeito;
- **extension**: catálogo e grants materializam descriptor/adapter; task
  authority ainda é obrigatória antes do stdio;
- **retomada**: checkpoint v2 é revalidado e não restaura authority;
- **troca de modelo**: provider fica atrás de `ModelGateway`.

`run --yes` satisfaz approval local da execução, não concede authority. A
administração canônica de catálogo/grants de extension ainda é programática;
`llm-agent tools` opera um registry legado e não configura automaticamente o
bootstrap CURRENT.

## Trust model e limites

- planning/discovery/availability não são permission;
- authority precede approval e execução;
- resultados externos são dados não confiáveis;
- stdio tem framing, timeout e limites, mas não é sandbox universal;
- o ScannerCore científico do TCC é externo ao Agent; os scanners locais do
  pacote não o implementam;
- MCP, arbitrary shell e isolamento universal de rede/filesystem não são
  fornecidos.

## Maturidade

Marcos 1 e 2 estão fechados. Marco 3 Block A está GREEN LOCAL; Blocks B/C não
foram concluídos e Standalone V1 ainda não foi declarada. A visão de uma
integração futura com o TCC permanece objetivo, não capability CURRENT.
