# Fechamento dos gates — Workbench F1

Data: 22/10/2026. Branch `feat/agent-workbench`; HEAD base
`9b5a2ddc2645430142e3c1ecaf6ef95c41e40406`. A identidade do candidato final e o
inventário integral estão no manifesto do ZIP de auditoria. O diagnóstico
registrado antes das mudanças está em
[`workbench-f1-gate-diagnosis-20261022.md`](workbench-f1-gate-diagnosis-20261022.md).
Logs anteriores e posteriores estão em
`prototypes/workbench-comparison/evidence/f1-integration-audit-20261022/`.

## Diagnóstico e correções mínimas

W21 e CURRENT inicialmente listavam 16 módulos `llm_agent.interactive` sem owner.
O mapa de caminhos já projetava o pacote extraído, mas as políticas de owner não
o classificavam. A projeção em memória demonstrou que `Application` é o owner
adequado: o pacote mantém sessão, roteamento, disponibilidade e mailbox neutros
de UI, coordena use cases da Application e não importa Tk ou CLI; essa
classificação produz zero arestas proibidas. Atualizei somente a família
`Application` nos mapas W22 e CURRENT. Baselines e direções de dependência não
foram alterados.

CURRENT ainda supunha que `_default_bindings` estivesse no adapter CLI. A
extração colocou a declaração única em `llm_agent.interactive.commands`, e o
adapter continua resolvendo os cinco handlers na CLI. Ajustei o checker para
seguir a importação compartilhada ao localizar a declaração e validar as mesmas
arestas da resolução dinâmica. Um teste estrutural cobre esse contrato; o
catálogo e os handlers de produção não foram movidos ou duplicados.

Ruff amplo encontrou I001 em `test_pre_pilot.py`, duas ocorrências de E701 no
mesmo arquivo e I001 em `evidence/cli-reuse-20261022/probe_neutral_reuse.py`.
São problemas de formatação nos próprios arquivos de evidência F1; corrigi
somente esses pontos.

## Regressão e gates

- W17: PASS.
- W21: PASS; zero violações, zero IDs novos, zero reintroduções; hash da projeção
  congelada preservado.
- CURRENT: PASS; 911 módulos e 3.193 arestas.
- Ruff amplo (`src`, `tests`, `scripts`, `prototypes/workbench-comparison`):
  PASS. O scanner ainda registra avisos `Acesso negado` ao percorrer diretórios
  temporários pytest protegidos pelo Windows; não alterei ACLs.
- Teste F1 offline: 84 passed.
- Regressão dirigida de CLI, compartilhamento, cancelamento, disponibilidade e
  integração Tk/WebView2: 189 passed. O teste específico do checker compartilhado
  também passou: 1 passed.
- Frontend: 4 cenários de corridas de submissão e 12 cenários de identidade,
  leitura e cancelamento do painel passaram. A matriz confirma 20 botões; 17 têm
  ação e três informam que ainda não estão implementados.
- `git -c core.whitespace=cr-at-eol diff --check HEAD`: PASS, sem alterar os
  arquivos CRLF existentes. O `git diff --check HEAD` padrão classifica o CR
  final desses arquivos como whitespace; a execução com `cr-at-eol` confirma
  que não há espaços finais reais.

Uma campanha adicional que coletava `tests/unit/runtime` inteiro e todos os
`tests/unit/interfaces` foi interrompida após vários minutos de CPU sem saída
parcial. Não é reportada como aprovada. A regressão dirigida acima cobre o
checker alterado, fluxos da CLI, disponibilidade, Tk e F1. A interrupção deixou
um diretório temporário pytest específico inacessível para remoção; foi
preservado, sem alteração de permissões, e excluído do ZIP como temporário.

A validação instalada do wheel já havia passado na etapa F1 anterior,
confirmando módulo, assets estáticos e entry point a partir do pacote instalado
([relatório F1](workbench-desktop-f1-20261022.md)). Uma repetição nesta rodada
parou no build tracker do pip com `PermissionError` ao gravar em um diretório
temporário local; o pip também não conseguiu remover seus diretórios temporários.
Nenhum ACL foi alterado. Esta limitação ambiental está registrada junto às
evidências; o código de produção e o wheel anteriormente validado não mudaram
durante o fechamento dos gates.

Não houve inferência real, carregamento de Qwen, alteração visual, commit,
merge, push ou publicação. A inspeção visual manual do usuário continua
separada dos gates automatizados.
