# Guia de contribuição e qualidade

Este documento define o padrão para mudanças que continuem fáceis de entender,
testar e estender por pessoas e por ferramentas de análise. As regras valem para
todo o código de produção. Exceções de complexidade e tamanho já registradas
em `quality/baseline.json` são dívida em ratchet, não autorização para novas
exceções.

## Princípios de projeto

1. **Responsabilidade explícita:** um módulo, classe ou função deve ter uma
   finalidade que caiba em uma frase curta. Separe coordenação, regra de negócio,
   acesso externo, serialização e apresentação.
2. **Dependências voltadas para dentro:** CLI e skills chamam casos de uso; casos
   de uso dependem de contratos e domínio; adapters implementam contratos. O
   domínio não conhece CLI, provider concreto nem mecanismo de persistência.
3. **Contratos tipados:** use dataclasses, enums, protocols e tipos de resultado
   nas fronteiras públicas. Dicionários livres pertencem às bordas de
   configuração e serialização e devem ser convertidos cedo para tipos do domínio.
4. **Efeitos visíveis:** leitura, escrita, processos, rede, uso de modelo e
   confirmação devem aparecer nas dependências ou no resultado. Evite estado
   global e efeitos escondidos em helpers.
5. **Erros úteis:** preserve causa, operação e contexto acionável. Não converta
   falhas diferentes em um `False` ou em texto genérico.
6. **Compatibilidade consciente:** fachadas legadas podem delegar ao fluxo novo,
   mas regras novas não devem ser duplicadas nelas.
7. **Autoridade e escopo explícitos:** toda operação recebe seu workspace e,
   quando produz efeito, uma porta de aprovação. `cwd`, confiança do modelo,
   configuração legada ou interação implícita com stdin não concedem
   autoridade.

## Organização e nomes

- Identificadores de código ficam em inglês. Textos de interface e documentação
  podem ficar em português.
- Prefira nomes do domínio (`ChangeSet`, `TaskGraph`, `ModelGateway`) a nomes de
  implementação (`Manager`, `Helper`, `Utils`).
- Funções coordenadoras devem delegar decisões a unidades pequenas e testáveis.
- Um comentário explica uma invariável, limitação ou decisão que o código sozinho
  não comunica. Não registre histórico de PR, edição ou conversa em comentários.
- Imports não podem inverter os limites descritos em
  [`docs/arquitetura-detalhada.md`](docs/arquitetura-detalhada.md). O gate
  arquitetural verifica as partes estáveis de `code`, `runtime`, `evaluation`,
  `llm` e `planning`.

## Tipagem e interfaces

- Toda API pública nova deve declarar tipos de entrada e saída.
- Use `Protocol` para portas substituíveis, sobretudo modelo, filesystem,
  validação, relógio e persistência.
- Diferencie estados no tipo quando eles alteram o fluxo; por exemplo,
  `succeeded`, `failed` e `unverified` não são booleanos equivalentes.
- Valide dados externos uma vez na borda e mantenha o núcleo trabalhando com
  valores válidos.
- Não acrescente um módulo a `[[tool.mypy.overrides]]` para concluir uma mudança.
  Corrija os tipos ou isole o trecho legado atrás de um contrato tipado.

## Testes

Teste comportamento observável, não a sequência interna de chamadas. Para cada
caso de uso relevante, considere:

- caminho de sucesso e falha;
- limites, timeout e cancelamento;
- efeitos reais em filesystem ou artefatos;
- ausência de efeitos em análise e review;
- negação de paths absolutos, `..`, symlinks e argumentos de subprocesso que
  escapem do workspace;
- ausência de stdin no modo headless e bloqueio de escrita sem autoridade;
- rollback e consistência após falha;
- implementação alternativa de uma porta, quando houver;
- restrições do perfil `low_vram_8gb` para operações de modelo.

Mocks são adequados nas bordas, mas não substituem um oráculo de efeito real. Uma
capacidade nova deve ganhar um cenário hermético em
`tests/fixtures/capabilities/` quando aplicável.

## Quality gates

Use `scripts/validate.py` como entrada local única para validação. O perfil é
obrigatório; `--plan` mostra os comandos do perfil sem executá-los. No Windows
PowerShell:

```powershell
.venv\Scripts\python.exe scripts\validate.py static --plan
.venv\Scripts\python.exe scripts\validate.py static
.venv\Scripts\python.exe scripts\validate.py tests
.venv\Scripts\python.exe scripts\validate.py installed
.venv\Scripts\python.exe scripts\validate.py all
```

Em Linux ou macOS, use o mesmo interpretador do ambiente virtual:

```sh
.venv/bin/python scripts/validate.py static --plan
.venv/bin/python scripts/validate.py static
.venv/bin/python scripts/validate.py tests
.venv/bin/python scripts/validate.py installed
.venv/bin/python scripts/validate.py all
```

`static` executa Ruff, a política de qualidade, mypy para as plataformas Linux
e Windows e `git diff --check`. `tests` executa `pytest -q`. `installed`
verifica o pacote instalado em ambiente limpo; não use
`--offline-diagnostic` como substituto. `all` executa todos os grupos mesmo
quando um falha e termina com status diferente de zero se algum falhar. Os
comandos são ancorados na raiz do repositório e usam o mesmo interpretador que
iniciou o runner.

Esses perfis são validações locais. `static` não substitui os testes; `tests`
local não equivale aos wrappers de aceitação do CI. A matriz de
[`.github/workflows/ci.yml`](.github/workflows/ci.yml) cobre Linux e Windows
com Python 3.10 e 3.12. Os scripts `scripts/w23_ci_windows.ps1` e
`scripts/w23_ci_linux.sh` são exclusivos dos runners descartáveis do GitHub
Actions: não os chame localmente nem simule `GITHUB_ACTIONS` para executá-los.
As duas plataformas do mypy ajudam a verificar APIs condicionais, mas não
substituem essa matriz de sistemas operacionais e versões de Python.

Os gates individuais continuam disponíveis para diagnóstico direcionado:

```sh
python -m ruff check .
python scripts/check_quality.py
python -m mypy --platform linux
python -m mypy --platform win32
python -m pytest -q
python scripts/verify_installed_package.py
git diff --check
```

`scripts/check_quality.py` verifica:

- complexidade C901 máxima 10 em `.` e tamanho máximo de 300 linhas para
  módulos de produção;
- nenhum módulo Python do projeto oculto por regras do `.gitignore`;
- direção de dependências nas camadas estáveis;
- links locais da documentação;
- arquivos de texto em UTF-8 sem BOM.

`check_quality.py` executa Ruff com C901 sobre `.` sem limitar a busca ao código
de produção. Separadamente, seu limite de 300 linhas se aplica aos módulos de
produção. O arquivo `quality/baseline.json` funciona como ratchet da dívida já
registrada.
As listas `allowed` desse baseline não estão vazias: elas registram exceções
preexistentes, não autorizam novas exceções nem tornam a dívida um padrão
aceitável. Não acrescente entradas ao baseline, `noqa` para C901 ou overrides
de mypy para contornar uma mudança.

## Mapa do repositório

- `src/`: código Python do pacote;
- `tests/`: testes e fixtures;
- `scripts/` e `quality/`: ferramentas, políticas e configuração dos gates;
- `quality/w23/`: registros e material histórico da Wave 23;
- `prototypes/workbench-comparison/`: comparação e evidências que ainda contêm
  código ativo; não trate o diretório inteiro como descartável;
- `runtime/`, `reports/` e `.temp_analysis/`: dados locais de runtime, relatórios
  e análises temporárias, não fontes de código do pacote;
- `.workbench-installed-*`: instalações locais geradas, também usadas pelo
  launcher local; confira seus consumidores antes de remover esses diretórios.

`.editorconfig` padroniza charset, indentação e final de linha nos editores.
`.gitattributes` mantém LF no repositório, inclusive quando o Git roda no
Windows. Arquivos de texto UTF-16 ou com BOM são rejeitados pelo gate.

## Definição de pronto

Uma mudança está pronta quando:

- a responsabilidade e a camada escolhidas são claras;
- contratos e efeitos novos estão tipados e documentados;
- os testes cobrem sucesso, falhas relevantes e limites;
- todos os gates locais passam;
- README, `docs/arquitetura-detalhada.md` e o guia específico refletem o comportamento
  público atual;
- não há segredo, artefato de runtime ou documento temporário versionado.
