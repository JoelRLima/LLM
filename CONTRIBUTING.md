# Guia de contribuição e qualidade

Este documento define o padrão para mudanças que continuem fáceis de entender,
testar e estender por pessoas e por ferramentas de análise. As regras valem para
todo o código de produção; não há módulos ou funções dispensados dos limites.

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

## Organização e nomes

- Identificadores de código ficam em inglês. Textos de interface e documentação
  podem ficar em português.
- Prefira nomes do domínio (`ChangeSet`, `TaskGraph`, `ModelGateway`) a nomes de
  implementação (`Manager`, `Helper`, `Utils`).
- Funções coordenadoras devem delegar decisões a unidades pequenas e testáveis.
- Um comentário explica uma invariável, limitação ou decisão que o código sozinho
  não comunica. Não registre histórico de PR, edição ou conversa em comentários.
- Imports não podem inverter os limites descritos em `EstruturaProjeto.md`. O gate
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
- rollback e consistência após falha;
- implementação alternativa de uma porta, quando houver;
- restrições do perfil `low_vram_8gb` para operações de modelo.

Mocks são adequados nas bordas, mas não substituem um oráculo de efeito real. Uma
capacidade nova deve ganhar um cenário hermético em
`tests/fixtures/capabilities/` quando aplicável.

## Quality gates

Execute antes de concluir:

```powershell
.venv\Scripts\python.exe scripts\check_quality.py
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy --platform linux
.venv\Scripts\python.exe -m mypy --platform win32
.venv\Scripts\python.exe -m pytest -q
git diff --check
```

As duas execuções do mypy são obrigatórias: elas validam APIs condicionais de
Linux e Windows mesmo quando o desenvolvimento ocorre em apenas um dos sistemas.

`scripts/check_quality.py` verifica:

- complexidade ciclomática máxima 10 em todo o código de produção;
- módulos com no máximo 300 linhas em todo o código de produção;
- nenhum módulo Python do projeto oculto por regras do `.gitignore`;
- direção de dependências nas camadas estáveis;
- links locais da documentação;
- arquivos de texto em UTF-8 sem BOM.

`quality/baseline.json` contém os limites e mantém as listas `allowed` vazias.
Uma entrada nessas listas, um `noqa` para C901, um override de mypy ou o
relaxamento dos limites não é uma forma aceita de concluir uma mudança.

`.editorconfig` padroniza charset, indentação e final de linha nos editores.
`.gitattributes` mantém LF no repositório, inclusive quando o Git roda no
Windows. Arquivos de texto UTF-16 ou com BOM são rejeitados pelo gate.

## Definição de pronto

Uma mudança está pronta quando:

- a responsabilidade e a camada escolhidas são claras;
- contratos e efeitos novos estão tipados e documentados;
- os testes cobrem sucesso, falhas relevantes e limites;
- todos os gates locais passam;
- README, `EstruturaProjeto.md` e o guia específico refletem o comportamento
  público atual;
- não há segredo, artefato de runtime ou documento temporário versionado.
