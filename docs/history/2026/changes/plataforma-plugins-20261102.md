# Plataforma de plugins locais — entrega de 02/11/2026

A entrega fecha o ciclo operacional de extensions locais de confiança no Workbench: registrar, habilitar por workspace, conceder/revogar capacidades, autorizar IDs específicos na sessão, revisar um manifest alterado e remover registros. Não há downloads automáticos, execução no registro, concessão automática de permissões ou cadastro no home real do usuário durante as provas.

A interface está em **Mais recursos → Plugins**. O [guia de uso e desenvolvimento](../../../plugins-locais.md) descreve o protocolo, os passos, a diferença entre estado persistente e autorização de sessão e os limites de segurança.

## Mudanças e propósito

- Novo serviço de aplicação delega aos owners existentes de catálogo e configuração de workspace, em vez de criar outra persistência.
- A sessão administra plugins somente ociosa e sem fila pendente. A bridge serializa essas operações com admissão normal, eventos pendentes e manutenção.
- O painel mostra origem, SHA-256 cadastrado e observado, estado do manifest, grants e códigos de diagnóstico, usando texto inerte.
- A autorização temporária seleciona IDs de extensions e fixa os fingerprints revisados no bootstrap. Um plugin com as mesmas capacidades de outro autorizado continua negado.
- Planejamento e gateway aplicam a restrição por ID. Grants de aplicação/tarefa, schema, modo, persona e approval permanecem obrigatórios.
- Alterações administrativas e troca de workspace limpam a autorização temporária. Revogar a seleção e desabilitar/grants são operações distintas.
- Atualização confere o fingerprint antigo e o novo revisado antes da publicação; grants novos não são concedidos implicitamente.
- Remoção do catálogo não apaga os arquivos da extension. Referências órfãs podem ser removidas da configuração local.
- O parsing de saída stdio rejeita chaves duplicadas, valores não finitos e overflow numérico. A compatibilidade histórica de status omitido foi preservada.
- A prova do launcher Windows acompanha o writer assíncrono atual e continua verificando que o programa externo não inicia antes da associação ao Job Object.

## Contrato de inicialização e compatibilidade

TaskAuthoritySnapshot ganhou um escopo opcional e imutável de IDs de extensions. A ausência preserva a semântica antiga da API/CLI. O Workbench usa esse escopo para as seleções explícitas do usuário; os builtins conservam sua política anterior, e modo/persona/approval continuam restringindo todas as invocações.

TaskExecutionStart ganhou dois campos nomeados e opcionais: IDs selecionados e fingerprints revisados. A ordem dos argumentos posicionais antigos foi preservada com kw_only. O contrato fechado C10 foi atualizado explicitamente para esses campos; novos checks rejeitam tipos genéricos, campos posicionais e autorização por padrão. Cinco testes negativos/positivos cobrem essa evolução. Os arquivos JSON de políticas e baselines não foram alterados nesta entrega.

Não é um mecanismo de hot reload. O runtime é encerrado e reconstruído após mudanças administrativas; o modo retorna a somente leitura. Os snapshots de outros processos/workspaces mantêm seu ciclo de vida existente.

## Validação

- **1.329 testes aprovados, 22 ignorados**, na suíte selecionada de ferramentas, interfaces, integração e contrato de inicialização.
- A prova Windows anteriormente quebrada passou; a outra correção de fixture fornece conversation explicitamente ao contexto do teste de modo. Nenhum guard de produção foi relaxado para fazer os testes passarem.
- **44 casos simulados** mantiveram resultados, efeitos, chamadas e contabilidade de tokens: 171 chamadas no padrão e 173 no compacto.
- **Arquitetura aprovada: 986 módulos, 3488 arestas.**
- Ruff passou nos arquivos alterados. Mypy isolado manteve os **37 diagnósticos preexistentes**, comparados por arquivo/mensagem com a versão anterior; os três módulos novos não acrescentaram diagnósticos. A configuração não foi enfraquecida.
- Cinco verificadores JavaScript existentes executados no Node REPL: comandos, histórico, painéis, botões e atualizações.
- Edge sem interface passou em três vistas do gerenciador, com ponte simulada: registro, grants separados, autorização explícita, atualização revisada, remoção, preservação de rascunho, clique duplo, ocupação, contexto obsoleto, erros e respostas atrasadas.
- Prova independente no Python isolado do pacote instalado: dois plugins temporários registrados, só um autorizado, outro negado antes de execução, demo executado, manifest revisado e remoção preservando arquivos.
- Não houve inferência real. Os testes executaram somente programas de fixture/demo em diretórios temporários.

Evidências: [pytest](../../../../reports/plugin-platform-20261102/tests.xml), [campanha](../../../../reports/plugin-platform-20261102/campaign.json), [comparação](../../../../reports/plugin-platform-20261102/comparison.json), [tipagem](../../../../reports/plugin-platform-20261102/mypy-baseline.json), [navegador instalado](../../../../reports/plugin-platform-20261102/installed-browser/browser-proof.json), [fluxos anteriores instalados](../../../../reports/plugin-platform-20261102/installed-existing-flows/browser-proof.json), [prova independente instalada](../../../../reports/plugin-platform-20261102/installed-proof.json).

## Instalação e limites

Revisão instalada: `c9fff8e9bc98-20261102T131435Z-1cfb0952`.
Anterior preservada: `61af1afa9087-20261102T122845Z-98f4290c`.
Wheel SHA-256: `1ad54a2bd2332191685debb8c2b314479c5166557909df6d7e847c79ccc728bd`.

A origem avaliada, a instalação e os fingerprints foram conferidos independentemente. Reabra o Workbench para carregar os novos controles.

O escopo é uma plataforma de plugins de ferramentas locais de confiança. Não fornece sandbox de filesystem/rede, assinatura ou integridade do pacote inteiro, marketplace, cliente MCP ou hooks científicos obrigatórios de política. O fingerprint protege a identidade do manifest, não todo o código executado. O método de análise do ScannerCore do TCC permanece externo; esta entrega fornece a integração e os controles do host.
