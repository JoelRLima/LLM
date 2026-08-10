# Health e diagnósticos

> **STATUS: CURRENT — PRIMARY HOME.** Operação do comando está em
> [operacao-standalone.md](../operacao-standalone.md).

`llm-agent doctor` executa checks read-only de Python/pacote, paths, workspace,
configuração, estado persistente e backend configurado. O relatório distingue
`read_write`, `read_only` e `unavailable`, além de readiness offline.

Os checks standalone não constroem o `Orchestrator` nem executam uma tarefa.
Escrever relatório só ocorre quando solicitado por `--write-report`. Checks
legados de runtime cobrem skills, logs, permissões e diretórios órfãos, mas não
substituem o diagnóstico do bootstrap de extensions.

Para extensions, catálogo, configuração, manifest, grants, drift e
materialização produzem diagnósticos próprios na inicialização. Uma extension
descoberta pode permanecer indisponível; health/diagnóstico não concede
authority nem prova que uma task authority a exporá.

Health não é benchmark de modelo, scanner científico, sandbox probe universal,
teste de rede abrangente ou garantia de que toda invocation futura terá sucesso.
