# ADR 0005: launcher interno para contenção de extensions stdio no Windows

- Status: aceito
- Data: 2026-08-01

## Contexto

`subprocess.Popen` inicia o processo da extension antes que o adapter possa
chamá-lo em `AssignProcessToJobObject`. Durante essa janela, a extension pode
executar imports, código de inicialização e criar descendentes. A stdin ser
enviada somente depois da associação não bloqueia esse comportamento.

## Decisão

No Windows, o adapter inicia um launcher Python interno, confiável e bloqueado.
O launcher é associado ao Job Object antes de receber o envelope privado que o
autoriza a iniciar a extension real. Se a criação ou a associação falhar, o
envelope não é enviado e a extension não é iniciada.

O launcher:

- usa somente a biblioteca padrão e não é uma tool, API pública ou entrada de
  manifest;
- recebe uma única linha UTF-8 com contrato privado versionado como
  `launcher_protocol: 1`;
- passa a command e a request pública já serializada para a extension sem
  reinterpretar o protocolo;
- herda o cwd, o ambiente operacional e os pipes do adapter;
- encaminha stdout e stderr diretamente por herança, sem um segundo sistema de
  drenagem;
- publica `extension_started` ou `launcher_error` em um arquivo JSON temporário
  privado, fora do workspace e fora do ambiente da extension;
- retorna o exit code da extension quando ela foi iniciada.

O protocolo público stdio continua em `1.0`, o manifest não muda e POSIX
continua iniciando a extension diretamente. `MAX_STDERR_BYTES` continua sendo
1 MiB. O launcher não é sandbox e não promete isolamento de filesystem, rede
ou APIs externas.

## Alternativas rejeitadas

- `CREATE_SUSPENDED` usando internals de `subprocess` ou handles privados;
- `NtResumeProcess`;
- sobrescrever `Popen._execute_child`;
- implementar `CreateProcessW` próprio neste gate;
- implementar `PROC_THREAD_ATTRIBUTE_JOB_LIST` próprio neste gate;
- associação best-effort com fallback para execução direta;
- sleeps ou polling para tentar vencer a corrida;
- redução silenciosa da garantia de contenção.

## Consequências

- um processo Python adicional é criado por invocação Windows;
- há pequena latência e consumo de memória adicionais;
- o launcher acrescenta complexidade interna, mas não exige mudanças nas
  extensions existentes;
- extension, filhos e netos iniciados após a liberação herdam o Job Object;
- timeout, limite e erros continuam usando o cleanup bounded existente;
- POSIX permanece no caminho direto, com teste de readiness antes do timeout;
- uma evolução futura pode considerar `PROC_THREAD_ATTRIBUTE_JOB_LIST` quando
  houver justificativa para uma camada nativa própria.
