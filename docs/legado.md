# Compatibilidade e retirada de legado

Este inventário impede que fachadas temporárias se tornem arquitetura
permanente. Código novo deve importar somente o caminho canônico. O gate
arquitetural rejeita imports dos aliases da raiz dentro de `agent/`.

| Compatibilidade | Caminho canônico | Consumidor restante | Condição de retirada |
| :--- | :--- | :--- | :--- |
| `cli.py` | `agent.interfaces.cli.app` | scripts e uso manual antigo | instalação pelo comando `llm-agent` adotada |
| `commands.py`, `command_*.py`, `cli_*.py` | `agent.interfaces.cli/` | imports externos antigos | nenhuma integração externa conhecida depender da raiz |
| `config.py`, `config_validation.py` | `agent.runtime.config*` | configurações e testes de terceiros | ciclo de migração anunciado antes da versão 1.0 |
| `logger.py`, `paths.py` | `agent.runtime.logging`, `agent.runtime.paths` | extensões antigas | extensões usarem as portas canônicas |
| `session.py` | `agent.llm.session` | integrações antigas | consumidores usarem `ModelGateway` ou a sessão canônica |
| `benchmark.py` | `scripts.benchmark` | comando manual documentado | documentação usar somente o módulo |
| `ModelClient` | `ModelGateway` | planejador linear/reativo | executores consumirem respostas tipadas do gateway |
| `AutoCoder` | `agent.code` e `code_task` | executor de plano legado | toda alteração passar por `ChangeSet` e validação |
| alias `git` | skill `git_reader` | planos persistidos antigos | checkpoints incompatíveis anteriores deixarem de ser suportados |

## Regras de migração

1. Não adicione funcionalidade nova a uma fachada.
2. Migre primeiro consumidores internos e mantenha teste de compatibilidade.
3. Registre quebra pública no changelog antes da retirada.
4. Remova fachada, teste e linha deste inventário no mesmo PR.
5. Não mantenha duas implementações: aliases apenas encaminham ao módulo
   canônico.
