# W23: diagnósticos manuais R01/R02 — implementação para revisão

## Estado e identidade

**NÃO EXECUTADO:** nenhum teste, benchmark, lifecycle, CI, Ruff ou mypy foi executado
para esta implementação. Os testes de regressão foram escritos e caberá ao proprietário
executá-los. Revisão estática manual não constitui aprovação funcional. W23 continua
**BLOCKED — NOT GREEN — UNVALIDATED**; nenhuma autoridade ou seletor foi ativado.

- Fonte: PR #2, base `5acccd7409071a8a6223f0f1899b63946b36bdb9`.
- Árvore base: `6075e843d836145b05774edc7f70cb4bc6aa0b82`.
- Handoff anterior autenticado: SHA-256 `5285cfe6962aee1ba07659bb30aae71ffabb2ae3f9471ff36639ba9177547d1d`.
- Contrato de 1.635 arquivos: `inputs.json`, SHA-256 `7b8299e3bec5c7180411008320d83a104f5331cfc9eea959abca25f46de0c497`.
- Patch experimental R02: `r02.patch`, SHA-256 `ad3529930ed28d5d8434111a8299f029b61209e092059eb6d7efbeda262df7f1`.
- O checker, materializador, verificador W18 e suas evidências históricas não foram alterados.
  Somente a segunda árvore descartável recebe o patch quando o proprietário executar R02.
- `.gitattributes` local preserva os bytes dos dois assets autenticados após checkout.

## Publicar a implementação revisada antes do dispatch

O PR atualmente referenciado não contém estes novos modos. O proprietário deve aplicar
`implementation.patch` em uma cópia limpa da base acima, revisar e publicar o commit no
branch de revisão. O agente não executou essas operações. Não modificar `main` nem
ativar autoridade. Exemplo de aplicação pelo proprietário, a partir da raiz dessa cópia:

```powershell
git apply --check C:\handoff\implementation.patch
git apply C:\handoff\implementation.patch
```

Os workflows existentes têm `push` amplo. Para publicar exclusivamente o diagnóstico
sem disparar essas campanhas, o proprietário pode usar `[skip ci]` na mensagem desse
commit de revisão. Isso não impede dispatch manual; não substitui os gates de aceitação.
Consulte [a documentação de skip](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/skip-workflow-runs).

O workflow `wave18-installed-product.yml` já existe em `main` com `workflow_dispatch`
(consulta somente leitura nesta implementação). GitHub exige o arquivo no branch padrão
para registrar o dispatch, mas permite selecionar outro branch por `--ref`. Os inputs
novos devem ser enviados explicitamente por CLI; a UI pode exibir o esquema de `main`.
Isso não foi confirmado por uma execução real neste trabalho.
[Documentação oficial](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow),
[contrato REST: ref branch/tag](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event).

## R01 — acionamento separado pelo proprietário

Substituir `$revision` pelo SHA de 40 caracteres do **novo commit revisado**, não pelo SHA
base. O guard compara esse SHA com `GITHUB_SHA`; movimento do branch provoca rejeição.

```powershell
$repo = 'JoelRLima/LLM'
$ref = 'review/w23-cand03-acceptance-20261015'
$revision = '<SHA de 40 caracteres do commit com esta implementação>'
gh workflow run wave18-installed-product.yml --repo $repo --ref $ref -f diagnostic_mode=r01 -f diagnostic_revision=$revision -f diagnostic_unit_tests=true
```

Somente `clean-install`, preparação e finalização originais. Nenhum outro cenário W18,
ConPTY, S1/S2 ou Qwen. Host Python 3.12.10 x64; runtime embarcado 3.12.14 conforme bundle.
Downloads e dependências serão obtidos apenas no runner descartável, com versões/locks
existentes e hashes dos artefatos fixados. O release-manifest deve reproduzir exatamente
`1f3cb01ca3f212a395162086d27678bf61989eb2e8847cd7900e9d64de5ac19f`.
Divergência interrompe a execução, sem trocar o pin.

## R02 — acionamento separado, após recolher R01

```powershell
gh workflow run wave18-installed-product.yml --repo $repo --ref $ref -f diagnostic_mode=r02 -f diagnostic_revision=$revision -f diagnostic_unit_tests=true
```

Python **3.10.11 x64 obrigatório**; indisponibilidade ou outra versão resulta UNKNOWN.
Duas cópias Git independentes da mesma base, sem remotes/hooks/alternates, fora do checkout
com a instrumentação. Patch aplicado somente em `patched`. Cada uma executa `check(ROOT)`
instrumentado e exatamente `test_current_checker_enforces_the_complete_repository_graph`.
A segunda executa também os três testes do patch. Não há pytest integral.

Perfis registram CPU, wall, AST parse/walk, análises de import dinâmico, custo inclusivo
por regra, resultados e hashes antes/depois. A origem do módulo e ROOT são verificados:
o profiler é iniciado por caminho absoluto, com cwd/PYTHONPATH da árvore de cada amostra.
Comparação de counts/errors e os testes focais evidenciam equivalência nesse corpus;
não provam equivalência universal ou resolução do timeout global da suíte.
Uma amostra por árvore não fundamenta alegação estatística de speedup.

`diagnostic_unit_tests=true` autoriza o runner a executar os 18 casos escritos para o
controlador antes da reprodução. O default é false. Incluem injeções/simulações e dois
casos Windows nativos, identificados no arquivo. Eles NÃO foram executados pelo agente.

## Alternativa segura se o GitHub rejeitar inputs no branch

Se a API retornar 422 por esquema/ref, **não reenviar sem diagnostic_mode**, pois isso
selecionaria o W18 normal. Não modificar `main` para desbloquear o diagnóstico.
O arquivo `manual-only.workflow.yml` é um template **inativo**, fora de `.github/workflows`.
O proprietário pode instalá-lo como `.github/workflows/w23-manual.yml` somente no branch
padrão de um repositório descartável separado. Ele tem exclusivamente workflow_dispatch.
Não copiar os workflows normais para esse repositório.

O template faz checkout do commit revisado de `JoelRLima/LLM`; se o repositório fonte for
privado, configurar nesse repositório descartável `SOURCE_READ_TOKEN` com acesso somente
leitura a contents da fonte. O token é usado apenas no checkout, persist-credentials=false;
não é encaminhado aos processos do diagnóstico nem incluído no artifact.

```powershell
$tempRepo = '<proprietario>/<repositorio-descartavel>'
$tempDefault = '<branch-padrao-do-repositorio-descartavel>'
gh workflow run w23-manual.yml --repo $tempRepo --ref $tempDefault -f diagnostic_mode=r01 -f diagnostic_revision=$revision -f diagnostic_unit_tests=true
# Recolher R01; em seguida, uma execução independente:
gh workflow run w23-manual.yml --repo $tempRepo --ref $tempDefault -f diagnostic_mode=r02 -f diagnostic_revision=$revision -f diagnostic_unit_tests=true
```

## Evidências para download e auditoria

No caso principal, `$runRepo=$repo`; na alternativa, `$runRepo=$tempRepo`.
Identificar cada run por evento workflow_dispatch, head SHA/branch e horário. Não usar
cegamente o último run do repositório. Conferir também a seção Selection do artifact,
pois na alternativa o head SHA do workflow é o do repositório descartável.

```powershell
$runRepo = $repo
gh run list --repo $runRepo --event workflow_dispatch --limit 10 --json databaseId,headSha,headBranch,workflowName,status,conclusion
$run = '<ID verificado de R01 ou R02>'
gh run watch $run --repo $runRepo
gh run view $run --repo $runRepo --json headSha,event,jobs,conclusion
# Escolher nome exato exibido no run (inclui attempt, normalmente 1):
gh run download $run --repo $runRepo --name "w23-r01-$run-1" --dir "C:\w23-evidence\r01-$run"
# Para o outro run: --name "w23-r02-$run-1" e diretório diferente.
```

Preservar o artifact original, seu digest exibido pelo GitHub e `SHA256.json` interno;
entregar ao auditor com URLs/IDs dos dois runs, SHAs selecionados e conclusões dos jobs.
Não editar os logs. Retenção configurada: sete dias. A página Summary do run também
permite download direto. O ZIP de implementação não contém resultados de execução.

- `summary.json`: PASS/FAIL/TIMEOUT/UNKNOWN, modo, integridade e W23 BLOCKED.
- `selection.json`, `environment.json`, inventários e SHA256: entradas e ambiente.
- `commands/`: comandos redigidos, stdout/stderr limitados, CPU e PIDs de Job Object.
- R01: checkpoints por função/operação `_v3_*`, subprocessos, heartbeats e stacks Python.
- R02: perfis por árvore, eventos por regra, hashes, JUnit resumido e comparação.

## Contenção, preservação e limites

Execução serial, Windows GitHub-hosted obrigatório. TEMP/TMP/TMPDIR, caches e basetemp
ficam em raiz inédita privada sob LOCALAPPDATA, NTFS local. Não alterar ACL de ancestrais.
Processos são criados suspensos, atribuídos a Job Object antes do resume e só então
executados. Limites: 48 processos, 4 GiB por job, 3 GiB por processo. Não usar taskkill
por nome, PID sem vínculo ou término de processos do host.

Comandos têm deadline agregado de 30 min: build 480 s, download/dependência 240 s cada,
R02 profile/teste nomeado 420 s cada, regressões do patch 60 s, testes do controlador
120 s; R01 cenário 180 s, finalização separada até 120 s, driver externo 360 s.
Step externo 32 min, job 45 min. Limites/capturas podem alterar o custo observado.

Cada stream tem teto de 2 MiB em memória e disco; linha acima de 16 KiB ou captura
incompleta impede PASS. Export público: até 256 arquivos, 4 MiB por arquivo, 128 MiB total.
Logs são incrementais/redigidos; nenhuma cópia de env, credenciais, configs ou árvores
privadas é enviada. Redação é defesa adicional: usar somente runners sem segredos de
produção. Artefatos são limitados; resíduos privados permanecem apenas na VM descartável.

Finalização e upload usam always(). Em cancelamento fatal/perda do runner o GitHub pode
não executar esses steps: ausência de evidência é UNKNOWN. Timeout fatal nunca prova
restauração; descartar o runner. PASS significa apenas o diagnóstico selecionado passou,
com hashes preservados; não libera W18 integral, CI ou W23. R01 não faz captura nativa de
stacks PowerShell/kernel: se parar dentro de um comando nativo, PIDs, comando, saída e
último checkpoint delimitam a operação, mas podem não identificar sua causa interna.
Snapshots de PIDs são periódicos e não constituem tracing completo de processos breves.

A ABI Win64, o uso de `_winapi` específico do CPython e a composição dos Jobs ainda
precisam dos testes nativos do proprietário. Não foi executado lint, tipagem, teste de
reaplicação do patch novo ou gate funcional. Nenhum hash histórico foi repinado.
