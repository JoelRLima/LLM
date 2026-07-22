# ADR 0001: limites do pacote e fachadas da raiz

- Status: aceito
- Data: 2026-07-22

## Contexto

CLI, configuração, sessão, logging e caminhos viviam na raiz. Módulos de
`agent/` dependiam desses arquivos, invertendo a direção natural do pacote e
deixando parte do código de produção fora da descoberta automática de tipos.

## Decisão

- interfaces de terminal ficam em `agent/interfaces/cli/`;
- configuração, logging e caminhos ficam em `agent/runtime/`;
- a sessão fica em `agent/llm/session.py`;
- a raiz conserva aliases sem lógica para compatibilidade;
- código dentro de `agent/` não pode importar esses aliases;
- `pyproject.toml` define pacote, dependências, extras e o comando `llm-agent`.

## Consequências

Existe um único endereço canônico por implementação, imports antigos continuam
funcionando durante a migração e todo o produto entra no mypy. A retirada dos
aliases segue [o inventário de legado](../legado.md).
