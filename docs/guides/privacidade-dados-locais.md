# Dados locais, retenção e compartilhamento

O projeto mantém conteúdo para dar continuidade ao trabalho e verificar efeitos.
Os formatos de observabilidade são projeções limitadas; os arquivos operacionais
podem conter objetivos, respostas e dados do projeto. Redaction reconhece padrões
de credenciais, mas não identifica todo dado pessoal ou comercial.

| Registro | Conteúdo | Local lógico | Retenção e cuidados |
| --- | --- | --- | --- |
| Configuração | Endpoint, perfil e possível credencial | `AppPaths.config_dir` | Necessária até alteração; não incluir em bundles |
| Log do Agent | Diagnósticos e exceções formatadas com redaction | `AppPaths.log_file` | Rotação de 2 MiB, até duas cópias anteriores; logs antigos não são reescritos |
| Diagnóstico do app | Mensagens e exceções redigidas | stderr do host | Depende de quem captura stderr; handlers já configurados pelo host continuam sob responsabilidade dele |
| Histórico de chat | Mensagens e respostas | `WorkspacePaths.chat_history_file`, ou destino explicitamente escolhido | Conteúdo operacional; revisar antes de exportar ou apagar |
| Memória e backups | Informações lembradas pelo Agent | Dados/memória e `memory_backup_dir` do workspace | Podem conter conteúdo de trabalho; preservar backups necessários à recuperação |
| Checkpoint | Estado de continuidade da tarefa | `WorkspacePaths.checkpoint_file` | Pode conter argumentos e observações; apagar somente ao descartar continuidade |
| Relatórios e artefatos | Resultado, validação e evidências | `reports_dir`, `artifacts_dir` | Revisão humana antes do compartilhamento |
| Traces | Eventos/diagnósticos redigidos, limitados e versionados | Diretórios de trace do workspace | Política própria de retenção; export não é removido pela retenção da trace |
| Bookmarks | Anotações do usuário | Estado de observabilidade | Podem conter texto sensível, mesmo quando a trace foi redigida |
| Fila de testes | Objetivos, seed, workspaces, resultados e oráculos | Diretório escolhido para a fila | Não apagar durante execução; é evidência do experimento |
| Worker independente | Progresso e diagnóstico da fila | `FILA/workers/ID.log` | Um log por lançamento; revisar e remover manualmente após encerrar/reter evidências |
| Cache | Dados derivados de ferramentas e dependências | Cache da aplicação e caches de desenvolvimento | O inventário da raiz só limpa três caches de desenvolvimento explicitamente permitidos |
| Checkpoint de revisão | Código/documentação e hashes | Destino escolhido no script de checkpoint | Não contém estado local ignorado; ainda assim pode conter conteúdo privado de código/docs |

## Exportar sem conteúdo

```powershell
llm-agent inspect export --workspace "C:\projeto" --metadata-only --output "C:\evidencias\metadados.zip"
```

O bundle contém IDs, sequência, origem e tempos dos eventos. Omite payloads,
snapshot de conteúdo, notas e mensagens de erro detalhadas. O manifesto marca
`privacy_mode=metadata_only`; os eventos projetados não constituem uma trace
reproduzível. `--include-bookmarks` é incompatível com esse modo.

O modo não muda a persistência operacional: memória, histórico, seed, resultados
e checkpoints continuam necessários ao funcionamento. Nenhum dado existente é
apagado ou redigido retroativamente.

## Inspecionar e limpar a raiz

`python scripts/repository_inventory.py --dry-run` classifica as entradas sem
ler configuração ou apagar arquivos. `--apply-cache-cleanup` remove somente
`.pytest_cache`, `.mypy_cache` e `.ruff_cache`, recusando links/reparse points.
Instalações, `.venv`, estado, protótipos e evidências são preservados. Encerre as
ferramentas que usam esses caches antes de aplicar a limpeza.

Consulte também [segurança](../agent/security.md),
[inspector](../observability-inspector.md) e [estrutura](../estrutura-diretorios.md).
