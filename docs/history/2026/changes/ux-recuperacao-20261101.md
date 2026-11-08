# Experiência do usuário: resultado claro e recuperação do pedido

Os artefatos locais citados neste documento não são versionados nem publicados com o repositório. Seus caminhos são referências da investigação original e não comprovam a validação do candidato atual.

## Problemas e mudanças

O botão **Sessão → Perfil e modelo** tinha o mesmo ID de um texto do resumo. Como o texto vinha antes do botão no HTML, o listener era associado ao elemento errado. O resumo agora usa `session-profile-description`, e o botão mantém sua identidade própria. O harness passou a exigir IDs únicos para não mascarar esse tipo de erro com um DOM simplificado.

Após falha, cancelamento ou resultado não verificado/parcial, um pedido textual de até 16.000 caracteres pode oferecer **Revisar pedido**. A ação recupera a mensagem para edição; não chama submit, enqueue ou control. Um rascunho diferente já presente é guardado na recuperação existente. Se exceder o limite de recuperação, o campo é preservado e a interface explica por que não o substituiu. A ação fica inativa durante outra execução e ao encerrar a sessão; pedidos de outro projeto não são restaurados no contexto atual.

O cartão explica o próximo passo em linguagem simples. Em envio não confirmado, recomenda conferir Tarefas antes de enviar novamente, pois a falha da confirmação não prova ausência de execução. Comandos `/...` e operações de inspeção não recebem esse atalho de recuperação. Não há retomada automática de ferramentas ou mudanças nas permissões.

Os estados principais aparecem em português: **Execução concluída**, **Execução não concluída**, **Execução bloqueada**, **Execução cancelada** e **Resultado não verificado**. Um erro explícito prevalece na apresentação mesmo se vier acompanhado de `status=succeeded`. Status e códigos originais continuam no detalhe do evento; respostas e relatórios originais permanecem disponíveis. Durante o trabalho, a mensagem passou de “Executando análise” para “Executando solicitação”, para também descrever pedidos de alteração.

Os eventos de erro e aviso tinham atributos de severidade, mas herdavam o verde dos eventos normais. Agora erro usa vermelho, aviso usa âmbar e eventos neutros usam cor neutra, nos temas claro e escuro. O ícone e o texto também distinguem o resultado, sem depender somente da cor. O tema claro exigiu corrigir a precedência do CSS para a borda e o fundo.

## Verificação

- **571 testes de interface e integração aprovados; 14 ignorados**. Resultado JUnit (artefato local: `reports/ux-recovery-tests-20261101.xml`). Houve um aviso Tk de descarte fora da thread principal, também observado na rodada anterior.
- Node não está no PATH, então os cinco harnesses foram executados separadamente no Node REPL disponível: botões, comandos, histórico, painéis e atualizações. Registro (artefato local: `reports/ux-recovery-node-20261101.json`). Incluem preservação de rascunho, ausência de reenvio, isolamento entre projetos, limite de tamanho, ação inerte durante execução e apresentação de erro com status conflitante.
- Edge headless, com bridge sintética, verificou o DOM real, o botão de perfil, revisão sem reenvio, recuperação do rascunho atual e ausência de overflow horizontal em duas larguras e temas. O teste consulta a cor computada do ícone e da borda de erro.
- Ruff e Mypy do script de verificação passaram. Nenhuma lógica de execução do agent ou capacidade de backend foi alterada.

O teste de navegador é automatizado e usa dados sintéticos; não substitui um estudo com usuários, não mede satisfação e não consulta modelo real. As verificações de DOM e navegação não representam uma medição de todos os comportamentos do host nativo WebView.

Para repetir a verificação de navegador:

```powershell
.venv/Scripts/python.exe scripts/verify_workbench_recovery_browser.py --output-dir reports/ux-recovery-browser-repeat
```

O script também aceita `--static-dir` para conferir os assets de uma instalação isolada. Os fixtures e perfis de navegador ficam em uma pasta nova dentro de `.tmp`; não usam a sessão nem a configuração real do usuário.

## Pacote instalado e conferido

Instalada `8bfafd3a2ca3-20261102T004713Z-01cf1901`, preservando `b749ff38cc0a-20261101T235324Z-37b40a6b` como anterior. O status confirmou `update_available=false` e o fingerprint do pacote coincide com seus metadados. SHA-256 do wheel: `840c09634f09f9cdd0623e32c37f5ee27856bc3efc4a794ea80835df208a3b10`.

A verificação Edge foi repetida usando diretamente os arquivos estáticos dessa instalação. Nas duas larguras, passou em IDs únicos, botão de perfil, cor computada de erro, revisão sem novo envio, preservação do rascunho e ausência de overflow horizontal. Provas do pacote instalado (artefato local: `reports/ux-recovery-installed-20261101/browser-proof.json`), captura escura (artefato local: `reports/ux-recovery-installed-20261101/desktop-dark.png`) e captura clara (artefato local: `reports/ux-recovery-installed-20261101/compact-light.png`).
