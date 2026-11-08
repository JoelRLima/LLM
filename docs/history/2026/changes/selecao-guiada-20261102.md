# Seleção guiada de projeto e perfil — 02/11/2026

Os artefatos locais citados neste documento não são versionados nem publicados com o repositório. Seus caminhos são referências da investigação original e não comprovam a validação do candidato atual.

O botão do projeto agora abre uma janela com projetos recentes e campo para a pasta existente. Em **Sessão → Perfil e modelo**, os perfis configurados aparecem com nome, provedor e modelo. Nenhuma dessas seleções ocupa o campo da conversa. A aplicação exige um clique explícito em **Aplicar seleção**.

O comando equivalente fica disponível em detalhes recolhidos. O envio usa `dispatchSubmission` e os comandos canônicos `/workspace switch` e `/model select`, mantendo admissão, settlement, validação da configuração e fechamento do runtime anterior. A seleção do perfil atualiza o perfil padrão pelo serviço existente. A troca de projeto continua recusada quando há pedidos pendentes e o novo projeto começa em READ_ONLY neste fluxo.

A consulta de opções usa o bloqueio da sessão e exige estado ocioso. Lê configuração e histórico local e retorna somente ID do workspace, nomes de perfis, provedor, modelo e caminhos recentes. Chaves, URLs e opções do provedor não integram essa projeção. Limites: até 100 perfis projetados e até 20 projetos recentes exibidos. Abrir a janela não consulta servidores nem testa geração.

A janela bloqueia envio durante trabalho, rejeita caracteres de controle, ignora respostas tardias após fechar e exige que o projeto consultado continue sendo o atual. Opções vazias e falhas de leitura aparecem sem aplicar uma seleção. A paleta de comandos continua disponível; adaptadores antigos sem essa consulta mantêm o caminho anterior.

## Validação

- Suíte de interfaces e integração: **729 aprovados, 16 ignorados**. A coleta incluiu cinco testes novos; o sexto foi acrescentado depois e passou no grupo focado de **6 testes**. Não são 735 testes únicos.
- Os seis testes focados passaram novamente após o ajuste final de tipagem. Incluem configuração real, troca de perfil e pasta com espaços, histórico atualizado e ausência de runtime do modelo.
- Cinco verificadores JavaScript existentes executados no Node REPL: comandos, histórico, painéis, botões e atualizações. O verificador de botões também carrega o novo módulo.
- Edge sem interface: seleção guiada verificada em três vistas, no código-fonte e no pacote instalado. Rascunho, caminhos com espaços, comando canônico, clique duplo, estado ocupado, fechamento durante leitura, projeto obsoleto, erro de configuração e perfis vazios cobertos. Capturas inspecionadas nos temas claro e escuro.
- Recuperação e fila também verificadas nos arquivos instalados, em três vistas, antes do ajuste final do texto sobre persistência do perfil.
- Ruff sem erros nos arquivos Python alterados. Arquitetura: **982 módulos, 3471 arestas**, política preservada.
- Mypy isolado dos três módulos: **30 diagnósticos preexistentes** de retorno Any. Comparação por arquivo e mensagem com a versão anterior confirmou zero diagnósticos acrescentados. A configuração da checagem foi preservada; não se declara aprovação integral de mypy.

Evidências: pytest (artefato local: `reports/ux-navigation-20261102/tests.xml`), seleção instalada (artefato local: `reports/ux-navigation-20261102/installed-browser/browser-proof.json`), fluxos anteriores instalados (artefato local: `reports/ux-navigation-20261102/installed-existing-flows/browser-proof.json`), comparação de tipagem (artefato local: `reports/ux-navigation-20261102/mypy-baseline.json`), verificação independente instalada (artefato local: `reports/ux-navigation-20261102/installed-proof.json`).

Para repetir a prova visual:

```powershell
.venv/Scripts/python.exe scripts/verify_workbench_recovery_browser.py --fixture tests/support/workbench_navigation_browser.js --output-dir reports/ux-navigation-repeat
```

As provas visuais usam ponte simulada. A prova independente usa o Python isolado do pacote e uma configuração temporária real para selecionar perfil e trocar projeto. Nenhuma avaliação chama modelos reais. Não se afirma ganho medido de usabilidade com usuários.

A revisão, a anterior e o SHA-256 do wheel constam da prova independente instalada. O gerenciador mantém versões anteriores, incluindo a versão anterior à rodada, `724c6ab37d11-20261102T014056Z-78bf3553`. Reabra o Workbench para carregar a interface instalada.
