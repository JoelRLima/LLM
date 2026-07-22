# Domínio de engenharia de código

## Visão geral

O domínio `agent/code/` concentra análise, geração, alteração, validação e
reparo. Ele não conhece CLI, `Orchestrator` ou provider concreto.

```text
ProjectDiscovery -> ProjectProfile
LanguageRegistry -> CodeIntelligenceService -> CodeAnalysis/RepositoryIndex
CLI / code_task -> CodingApplicationService -> CodingWorkflowService
                                            -> ContextSelector
ModelGateway --------------------------------> ChangeSetTransaction
                                            -> ChangeApprovalPolicy
                                            -> ProjectValidator
                                            -> FailureClassifier -> TaskResult
```

## Descoberta de projeto

`ProjectDiscovery` identifica:

- linguagens por extensão;
- manifests como `pyproject.toml`, `package.json`, `tsconfig.json`, `Cargo.toml`
  e `go.mod`;
- raízes de source e testes;
- presença de Git;
- truncamento por limite de arquivos.

Pastas de dependência, runtime, build e cache são ignoradas. Descobrir um
manifest não autoriza executar seus scripts.

## Adapters de linguagem

`LanguageRegistry` escolhe um adapter por caminho. O adapter Python usa `ast` e
produz símbolos, assinaturas, imports, erros de sintaxe e diagnósticos básicos de
chamadas perigosas. Arquivos sem adapter usam `GenericTextAdapter`:

- nível `textual`;
- confiança baixa;
- nenhuma afirmação de análise semântica;
- leitura e busca continuam disponíveis.

Adicionar uma linguagem não exige alterar `CodeIntelligenceService`.

## Inteligência de código

`CodeIntelligenceService` oferece:

- análise de arquivo;
- índice do repositório;
- busca de símbolos por nome;
- diagnósticos por arquivo e linha;
- cache por SHA-256;
- isolamento da raiz e limite de tamanho por arquivo.

Falha de parsing de um arquivo é um diagnóstico e não interrompe o índice dos
demais arquivos.

## Seleção determinística de contexto

`ContextSelector` escolhe no máximo seis arquivos, com orçamento total de
texto. O ranking não depende do modelo:

1. targets explícitos;
2. arquivos contidos em um target de diretório;
3. nomes de arquivo mencionados no objetivo;
4. símbolos mencionados;
5. imports dos targets explícitos.

Cada trecho informa caminho, motivos da seleção e SHA-256 dos bytes atuais.
Arquivos grandes usam cabeçalho e regiões dos símbolos relevantes. As quebras
de linha são preservadas exatamente, inclusive CRLF no Windows, para que
`base_hash` e `expected_text` possam ser usados como precondições reais.

## ChangeSet

A API pública permanece em `changes.py`, enquanto a implementação separa
contratos (`change_models.py`), parsing e validação estrutural
(`change_parsing.py`) e transação de arquivos (`change_transaction.py`). Assim,
alterar o formato aceito pelo modelo não mistura regras de I/O e rollback.

O modelo nunca recebe autorização implícita para escrever. Ele propõe:

```json
{
  "objective": "Adicionar função add",
  "rationale": "Implementação mínima",
  "changes": [
    {
      "path": "math_utils.py",
      "kind": "create",
      "content": "def add(a, b):\n    return a + b\n"
    }
  ]
}
```

Tipos suportados: `create`, `modify`, `edit`, `delete` e `move`. `edit` descreve
operações `replace`, `insert_before`, `insert_after` ou `delete` por faixa de
linhas do conteúdo original:

```json
{
  "path": "math_utils.py",
  "kind": "edit",
  "base_hash": "<sha256 exibido no contexto>",
  "edits": [
    {
      "operation": "replace",
      "start_line": 2,
      "end_line": 2,
      "expected_text": "    return a - b\n",
      "content": "    return a + b\n"
    }
  ]
}
```

As faixas são referenciadas ao arquivo original, verificadas contra
`expected_text`, rejeitadas se forem sobrepostas e aplicadas de baixo para cima.
`base_hash` protege o arquivo inteiro. Divergência em qualquer precondição gera
conflito antes da escrita. Caminhos fora da raiz e arquivos duplicados são
rejeitados. `modify` integral continua disponível como fallback, mas recebe
menor confiança que um `edit` localizado.

Estados:

```text
proposed -> staged -> committed -> validated
                      |
                      +-> rolled_back
```

O transaction captura backups limitados, produz unified diff, revalida o
snapshot antes do commit e usa replace atômico por arquivo. Se o arquivo mudar
entre a prévia/confirmação e o commit, a alteração externa é preservada e o
ChangeSet falha por conflito. A garantia cobre apenas os arquivos descritos no ChangeSet;
não cobre efeitos arbitrários de shell, processos ou rede.

## Confiança e confirmação

`ChangeApprovalPolicy` calcula confiança sem consultar o modelo. Reduzem a
confiança: arquivo fora dos targets declarados, arquivo existente sem
`base_hash`, reescrita integral, edit sem `expected_text`, operações destrutivas,
conteúdo muito grande e quantidade de arquivos acima do limite. Os defaults são:

```json
{
  "code_policy": {
    "auto_apply_min_confidence": 0.85,
    "max_auto_files": 2,
    "require_target_alignment": true
  }
}
```

Abaixo do limiar, a transação permanece somente em stage e retorna `blocked`
com diff, score e motivos. A CLI mostra a prévia e pede confirmação. A skill só
aprova automaticamente se `auto_confirm` estiver explicitamente habilitado.

## Validação

`ProcessRunner` executa arrays de argumentos com `shell=False`, cwd restrito ao
projeto, timeout, cancelamento e limite de saída. Ele não instala pacotes.
O runtime de subprocessos fica em `validation_process.py`; descoberta e
seleção de validators permanecem em `validation.py`.

Status possíveis:

- `passed`;
- `failed`;
- `unavailable`;
- `cancelled`;
- `timed_out`.

O provider Python executa `py_compile` para arquivos alterados e, quando
solicitado, pytest nas raízes de teste descobertas. Outros ecossistemas devem
adicionar um `ValidationProvider`; o core não deve ganhar condicionais por nome
de ferramenta.

## Workflows

`CodingWorkflowService` implementa:

- `analyze`: análise determinística, sem modelo;
- `review`: análise somente leitura e verificação de que arquivos não mudaram;
- `generate`: proposta, aplicação, validação e resultado;
- `modify`: mesmo pipeline para arquivos existentes;
- `refactor`: mesmo pipeline com objetivo de preservar invariantes;
- `repair`: ciclo limitado com diagnóstico e rollback entre tentativas.

`workflows.py` conserva a fachada e o fluxo de alto nível. A construção e o
parsing de propostas ficam em `workflow_proposal.py`, e aplicação, validação
e rollback ficam em `workflow_application.py`.

Antes de uma nova tentativa, `FailureClassifier` classifica de forma
determinística sintaxe, teste, timeout, cancelamento, ferramenta indisponível,
conflito, saída estruturada, permissão ou falha desconhecida. Cancelamento,
permissão e ferramenta indisponível não disparam reparo automático. Propostas
idênticas que já falharam não são repetidas.

Resultados usam `TaskResult`:

- `succeeded`: mudança validada;
- `unverified`: aplicada, mas não há validator disponível;
- `failed`: falha de modelo, parsing, conflito, aplicação ou validação;
- `cancelled`;
- `blocked`.

`unverified` não deve ser apresentado como “testes passaram”.

Somente validators e o runtime escolhem `succeeded`/`unverified`/`failed`. Texto
do modelo não pode promover o estado da tarefa nem dispensar confirmação.

## Comandos explícitos `/code`

`commands.py` converte a sintaxe diretamente em `CodeRequest` e chama
`CodingApplicationService`; `Orchestrator`, router e planner não participam:

```text
/code analyze [arquivo]
/code review <arquivo...>
/code generate [targets...] -- <objetivo>
/code modify <targets...> -- <objetivo>
/code repair <targets...> --tests -- <objetivo>
/code refactor <targets...> -- <objetivo>
/code template parallel_analyze <arquivo...>
/code template parallel_review <arquivo...>
/code template analyze_then_modify <arquivo...> -- <objetivo>
```

`--yes` é uma aprovação explícita de propostas de baixa confiança. Caminhos com
espaços devem usar aspas; prefira `/` como separador para portabilidade.

Análise e review funcionam mesmo sem provider configurado. As ações que propõem
mudanças consomem o orçamento compartilhado de chamadas e falham explicitamente
se o gateway estiver indisponível.

## Skill `code_task`

Argumentos principais:

```json
{
  "action": "modify",
  "objective": "Normalize espaços sem alterar a assinatura pública",
  "targets": ["text.py"],
  "include_tests": true
}
```

A skill recebe `ModelGateway` e configuração na composição da CLI. Ela não
recebe `Orchestrator`. CLI e skill compartilham `CodingApplicationService`, que
é a entrada única para construir contexto, policy, workflow e scheduler. A
action `template` cria grafos determinísticos; `multitask` aceita um `TaskGraph`
manual, ambos documentados em `multitarefa.md`.

## Como adicionar um adapter de linguagem

1. Implemente `name`, `extensions`, `supports` e `analyze`.
2. Retorne contratos normalizados, incluindo limitações.
3. Registre no `LanguageRegistry`.
4. Se houver comandos, crie um `ValidationProvider` separado.
5. Adicione fixture com código válido, inválido e arquivo grande.
6. Não carregue parser/modelo pesado no import do pacote.

## Testes relacionados

- `tests/unit/code/test_code_intelligence.py`;
- `tests/unit/code/test_changeset.py`;
- `tests/unit/code/test_project_validation.py`;
- `tests/unit/code/test_coding_workflows.py`;
- `tests/unit/code/test_code_assistance.py`;
- `tests/integration/test_capability_evaluation.py`.
