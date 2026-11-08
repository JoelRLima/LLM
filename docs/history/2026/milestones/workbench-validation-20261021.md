# Validação do Workbench — 21/10/2026

Branch: `feat/agent-workbench`. Base Git: `9b5a2ddc2645430142e3c1ecaf6ef95c41e40406`.
A árvore preserva as alterações locais de UX da CLI validadas anteriormente.
Implementação e fontes: [agent-workbench.md](../../../agent-workbench.md).

A fatia usa o Agent real com gateway de teste em memória. Os cenários verificam
leitura efetiva de arquivo, persistência do relatório pelo owner existente,
igualdade do conteúdo aberto com o arquivo original, correlação de execução e
preservação da conversa ao abrir, fechar e reabrir a aba Tk. As ferramentas não
são simuladas. Não houve chamada a Qwen ou outro endpoint real.

Resultados já concluídos nesta rodada:

- Regressão dirigida: **523 passed, 9 skipped** em 6,70 s (rodada final).
- Inclui interfaces CLI/Workbench, outputs, task execution, conversation,
  application projection e integrações de relatório e outputs.
- Os dois cenários de análise real e janela Tk passaram; o teste de GUI não foi ignorado.
- Ruff em `src`, `tests` e `scripts`: aprovado.
- mypy Linux e Windows: aprovado, 994 arquivos por plataforma.

Gate de qualidade: aprovado. Instalação do wheel em ambiente limpo: aprovada.
Nenhuma conclusão desta fatia altera aceitação, hashes ou evidências históricas
W23. A suíte global não foi reexecutada nem declarada verde nesta rodada.

A projeção específica do Workbench foi isolada em application/workbench/submission.py.
O arquivo histórico application/_task_observation.py foi preservado byte a byte,
sem normalização adicional de CRLF nem alteração de seu contrato para a CLI.

Evidências locais da rodada final:

- `.tmp/workbench-final-quality.log`
- `.tmp/workbench-final-installed.log`
- `.tmp/workbench-final-installed-summary.json`
- `git diff --check`: aprovado.
- Após ajustar a coleta para ambientes sem o módulo opcional Tk, os três testes
  de integração passaram novamente em 1,76 s; Ruff do arquivo também passou.

O gate de qualidade preservou a dívida existente: complexity_debt=24 e
oversized_modules=32. Foram observados 1464 arquivos de fonte visíveis e 894
módulos de arquitetura. Nenhuma baseline foi relaxada.

A documentação recebeu os resultados após os gates. Os fontes de produção
permaneceram idênticos ao candidato final empacotado. A revisão visual humana
continua sendo uma etapa distinta dos testes automatizados de widgets.

Wheel SHA-256: 37866637d4574b4863d3524f6ca73e37282e2f6b2ecd3ed009d070fafe126a94.
