# Arquivo local fora do checkout

Campanhas históricas e temporários de validação podem ser consultados em
`D:\LLM Agent Harness Archive\2026-10-25\`.
Este caminho pertence a esta máquina e não é necessário para instalar o pacote.

- `relocated-root/`: pastas retiradas fisicamente da raiz do projeto.
- `PLAN.json`: origens e destinos planejados.
- `RESULTS.json`: resultado de cada tentativa, erros de leitura e verificação.
- `*.files.json`: tamanho e SHA-256 dos arquivos que puderam ser lidos antes da movimentação.

As campanhas movidas são `f1-autonomous-close-20261024`,
`investigacao-generalizacao-20261023`, `p3-final-acceptance-20261023`,
`prm-gate-evidence` e `prm-v002-final-evidence`. Temporários de pytest e
empacotamento também estão relacionados em `RESULTS.json`.

Em 25/10/2026, as 20 pastas planejadas foram movidas e deixaram de existir na
raiz original. Foram conferidos 40.958 arquivos por tamanho e SHA-256 antes e
depois, totalizando aproximadamente 3,6 GB legíveis, sem divergências.
Os diretórios `.pytest_temp/` e `.pytest_tmp/` tiveram 101 erros de leitura de
descendentes no inventário; foram movidos inteiros, sem alterar permissões,
mas esses descendentes inacessíveis não puderam ser verificados por hash.
Nenhum arquivo rastreado foi excluído, e os sete worktrees registrados foram
preservados. Os quatro repositórios Git internos das campanhas movidas
continuaram conseguindo ler seus commits HEAD.

O arquivamento usa uma mudança de localização da pasta inteira no mesmo volume,
sem reescrever os documentos históricos ou alterar permissões. As referências
absolutas antigas dentro de evidências permanecem como registros históricos.
Scripts históricos que dependam da localização original podem exigir restauração
antes de serem executados; não use snapshots como substitutos do código atual.
Pastas com erros de leitura preservam esses limites de acesso no destino.

Os repositórios internos da campanha F1 continuam dependendo de
`D:\LLM Agent Harness\.git\objects`, por caminhos absolutos já existentes.
Portanto, o arquivo externo é acessível localmente, mas não é um repositório
independente e não deve ser transportado como tal sem uma migração Git própria.
Também não é uma cópia de segurança contra falha do disco D:.

Para restaurar uma pasta, consulte sua origem em `RESULTS.json`, confirme que
esse caminho não existe e mova somente a pasta correspondente de volta.
Não sobrescreva uma pasta recriada. Os manifests permitem conferir os arquivos
legíveis; descendentes inacessíveis não têm comprovação por hash.

Permanecem no projeto: `.temp_analysis/` ignorada, `.tmp/` com seus worktrees,
o ambiente `.venv/`, a instalação local do Workbench, configuração e estado
local, código, testes, protótipos ainda usados e ferramentas do projeto.
