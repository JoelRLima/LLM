# Correções de consistência — 26/10/2026

Os seis achados da investigação foram corrigidos preservando os contratos,
os owners de execução e as alterações anteriores do checkout.

| Achado | Comportamento corrigido | Regressão |
| --- | --- | --- |
| `last_result` obsoleto no checkpoint | Havendo histórico, somente seus resultados determinam a ordem das evidências. O fallback para `last_result` atende apenas históricos vazios. | Round-trip real do checkpoint com última mutação sem validação e `last_result` antigo aprovado; compatibilidade de resultado terminal também coberta. |
| Busca retornava vazio após falha de acesso | Pasta raiz ilegível retorna erro; falhas parciais em subpastas/arquivos preservam os matches disponíveis e marcam a consulta como incompleta. | Permissão negada na raiz, subpasta e arquivo, além de ausência genuína de matches. |
| Detalhes finais calculados sobre as primeiras 50 observações | Fatos finais usam o histórico completo; a apresentação de ferramentas, passos e invocações mantém sua amostra limitada. | Observação 51 com saída, resultado de código e validação distintos; imutabilidade após publicação. |
| Corte de bytes dividia um caractere UTF-8 | Leitura e busca compartilham decoder incremental estrito que preserva o prefixo completo quando há truncamento. Bytes realmente inválidos continuam distinguíveis. | Caracteres `é` e emoji atravessando o limite; leitura inválida e busca de diretório com resultados parciais. |
| Dependências perdiam invariantes/evidências | A projeção da dependência usa todos os campos declarados da fase, mantendo as identidades dos requisitos da Spec completa. | Contexto de fase selecionada, referências estáveis e definição persistida inalterada. |
| Workbench inacessível após a primeira página | Histórico em páginas de 50; detalhes consultados diretamente pelo ID retido através da inspeção canônica, confinada ao workspace. | 51 registros reais, duas páginas sem perda/duplicação, detalhe antigo, ID ausente e workspace diferente. |

## Verificação

- A suíte integrada terminou com **1.634 testes aprovados e 3 skips**.
  Dois skips dependiam de privilégio para criar symlinks no Windows. O terceiro
  dependia de um executável Node no PATH.
- O mesmo harness JavaScript desse terceiro teste foi executado pelo Node REPL
  e passou: paginação, deduplicação, botão desabilitado durante a requisição e
  resposta antiga descartada após refresh.
- Depois da extração da validação de padrões para respeitar C901, os **24 testes
  de consultas** foram executados novamente e passaram.
- Ruff no código, scripts e testes, mypy nas plataformas Linux/Windows e
  `git diff --check` passaram. Os módulos de produção alterados permanecem com
  até 300 linhas; nenhuma exceção foi acrescentada ao baseline.
- Os gates globais continuam sujeitos às pendências anteriores em
  `prototypes/workbench-comparison`; elas não foram ocultadas ou reescritas.

O harness da interface está em
[`tests/support/workbench_history_ui.cjs`](../../../../tests/support/workbench_history_ui.cjs).
Com Node disponível no PATH, ele é executado pelo teste
[`test_workbench_history_ui.py`](../../../../tests/unit/interfaces/test_workbench_history_ui.py).
Esse teste exercita a lógica de estado da interface, sem abrir o host nativo
WebView2.

As regras atualizadas de evidência e projeção estão em
[previsibilidade de tarefas](../../../previsibilidade-tarefas.md), e o uso da paginação
está no [guia do Workbench](../../../agent-workbench.md).
