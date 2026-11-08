# Estrutura de diretórios

> Mapa físico CURRENT do repositório. Para fronteiras e responsabilidades entre módulos, consulte a [arquitetura detalhada](arquitetura-detalhada.md) e o [índice técnico](README.md).

## Raiz do repositório

```text
LLM Agent Harness/
|-- .github/workflows/       # CI e aceitação de release
|-- docs/                    # guias, contratos, ADRs, pesquisa e histórico
|-- examples/                # exemplos de configuração e integração
|-- installer/               # instalação do Workbench
|-- distribution/            # código versionado para formar payloads
|-- quality/                 # políticas, baselines e evidências congeladas
|-- scripts/                 # validação, release, diagnóstico e campanhas
|-- src/llm_agent/            # pacote Python instalável
|-- tests/                   # unitários, integração, policy e regressão
|-- prototypes/              # protótipos e evidências de comparação
|-- pyproject.toml            # metadados e dependências canônicas
|-- requirements-ci.lock      # conjunto fixado para CI
|-- requirements.lock         # perfil fixado de desenvolvimento + ML
|-- config.example.json       # exemplo versionado de configuração
|-- README.md                 # entrada de produto e instalação
|-- CONTRIBUTING.md           # regras de contribuição e quality gates
|-- Workbench.cmd             # menu único: abrir, gerenciar, baterias e validar
`-- Gerenciar_Workbench.ps1   # implementação do gerenciador Windows
```

## Pacote e validação

`src/llm_agent/` é a única fonte do pacote instalado. Seus pacotes de primeiro nível são:

- `agent/`: domínio, planejamento, orquestração, runtime e ferramentas;
- `application/`: casos de uso compartilhados pelas interfaces;
- `interfaces/`: CLI e Workbench;
- `actions/`, `discovery/`, `execution/`, `extensions/`, `filesystem/`,
  `health/`, `interactive/`, `outputs/`, `process/`, `storage/` e `workspace/`:
  adapters e serviços com fronteiras próprias;
- `resources/`: defaults e dados incluídos no pacote.

`tests/` espelha essas fronteiras quando aplicável. `fixtures/` e `support/` contêm
recursos compartilhados; `policy/` verifica regras do repositório; `integration/`
verifica fluxos compostos; `unit/` e `regression/` cobrem comportamento local e
regressões específicas.

`quality/` contém baselines e políticas. `scripts/` guarda executáveis de qualidade,
benchmark e release; subpastas como `w21_architecture/`, `w22_architecture/` e
`w23_diagnostics/` agrupam implementações auxiliares por campanha. Os gates
permanentes são descritos em [`CONTRIBUTING.md`](../CONTRIBUTING.md).

## Documentação

- [`README.md`](README.md): índice e rotas de navegação;
- [`arquitetura-detalhada.md`](arquitetura-detalhada.md): módulos e fronteiras;
- `agent/`: contratos CURRENT por subsistema;
- `guides/`: procedimentos de uso;
- `adr/`: decisões arquiteturais;
- `research/`: avaliações e recomendações datadas;
- `history/`: entregas e marcos históricos.

## Arquivos locais e artefatos

`build/`, `dist/`, `.venv/`, caches de ferramentas, `runtime/` e `reports/` são
saídas locais ignoradas pelo Git. `distribution/` é diferente de `dist/`: o
primeiro contém código versionado usado no processo de distribuição.

`prototypes/workbench-comparison/` reúne protótipos e evidências históricas,
mas ainda contém código e material de comparação útil. Não é diretório de
cache e não deve ser removido por rotinas de limpeza genéricas.
