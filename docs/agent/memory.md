# Memória

> **STATUS: CURRENT — PRIMARY HOME.** Este documento separa persistência
> entregue de enriquecimento opcional.

## CURRENT

`AgentMemory` mantém estado por workspace. A composition root injeta o snapshot
JSON, o SQLite e o diretório de backups a partir de `WorkspacePaths`.

- o construtor não abre banco nem cria diretórios; `initialize()` pertence ao
  bootstrap;
- JSON é lido estritamente e promovido por escrita temporária + `os.replace`;
- summaries persistidos têm SQLite como fonte canônica;
- corrupção durante bootstrap falha fechada, em vez de substituir estado válido
  por defaults silenciosos;
- o fluxo principal de `ContextManager.build_context()` serializa a memória
  ativa no contexto; ele não usa seleção por objetivo nem um budget próprio;
- `AgentMemory.get_context_for_prompt(objective, budget_tokens=800)` oferece uma
  seleção compacta por objetivo, mas é foundation disponível e ainda não está
  ligada ao main-prompt CURRENT;
- backups de memória são distintos de restore points do workspace.

Essa persistência existe e atravessa processos no mesmo workspace. Ela não é
memória global entre workspaces, banco vetorial remoto ou authority source.
Conteúdo lembrado e output de tool nunca concedem capability.

## OPTIONAL / graceful degradation

`SemanticMemory` pode indexar summaries com `sentence-transformers` e salvar ou
carregar o índice. `ContextManager` sempre constrói a facade e
`get_file_hints()` tenta consultá-la; não existe config gate CURRENT para esse
wiring. Modelo e índice são carregados/construídos de forma lazy somente quando
há consulta e summaries. Sem os extras opcionais `numpy` e
`sentence-transformers`, ou sem summaries, a busca degrada para uma lista vazia
e o restante do runtime continua funcionando. A camada semântica não é
requisito da instalação core.

## NOT PROVIDED

Não há serviço remoto de memória, sincronização multiusuário, MCP memory server,
retenção/criptografia universal nem garantia de recuperação contra corrupção
externa do filesystem.
