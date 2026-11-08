# Plugins locais: usar e desenvolver

Antes de autorizar um plugin na sessão, o gerenciador mostra comando como argv,
diretório, identidade do usuário atual, grants e SHA-256 do manifest conferido.
A autorização solicita revisão explícita dessas informações. Um manifest que
mudou ou não pôde ser conferido não recebe autorização por esse caminho.

O perfil é **processo local confiável**, sem sandbox. O ambiente inicial é
filtrado por allowlist, mas isso não restringe o acesso do processo a arquivos
fora do projeto ou à rede permitida pelo sistema. O fingerprint cobre o
manifest, não fixa executável ou dependências. O teste controlado de fronteira
comprova tanto a filtragem do ambiente quanto esse limite de acesso a arquivos.

O Workbench oferece administração de extensions locais pelo menu **Mais recursos → Plugins**. O protocolo é stdio próprio, versão 1.0. Esta superfície é para código de confiança; não é um marketplace, um cliente MCP ou uma sandbox para programas hostis.

## Usar um plugin

1. Clique em **Escolher arquivo JSON** ou informe o caminho completo do manifest e clique em **Registrar manifest**. Isso lê e valida os metadados, sem iniciar o programa.
2. Clique em **Habilitar** para este workspace.
3. Conceda, separadamente, as capacidades declaradas que você pretende autorizar. Toda ferramenta stdio exige `process`; um scanner de arquivos normalmente também declara `read`.
4. Confira origem e fingerprint nos detalhes. Clique em **Autorizar nesta sessão**. Somente os IDs selecionados são admitidos para uso pelo modelo; outro plugin com capacidades iguais continua bloqueado.
5. As alterações reiniciam a execução em somente leitura. Quando precisar de processos, use **Sessão → Modo de trabalho** e selecione explicitamente `/mode full`. FULL continua limitado pelos grants, IDs selecionados, políticas de persona, validações e aprovações.
6. Faça o pedido no chat. Autorizar um plugin não executa sua ferramenta nem obriga o modelo a escolhê-la.

O rascunho da conversa permanece no lugar durante a administração. Tarefas ativas, eventos ainda não consumidos e pedidos pendentes impedem alterações. As autorizações temporárias são limpas ao trocar de workspace ou alterar catálogo/configuração/permissões. Elas não são salvas como autorização permanente nem concedidas pelo modelo.

O botão **Retirar da sessão** remove só aquela seleção. **Limpar autorizações da sessão** remove todas. **Desabilitar** mantém os grants persistidos; **Revogar** remove a capacidade correspondente.

## Atualizar e remover

Quando os bytes do manifest mudam, o gerenciador mostra o estado alterado, o fingerprint cadastrado e o observado. **Aceitar manifest revisado** valida o fingerprint novo escolhido e o antigo cadastrado antes de substituir a entrada. Uma nova mudança entre a revisão e a preparação é recusada. Capacidades novas não recebem grant automático. As autorizações de sessão são limpas.

**Remover do catálogo global** remove o registro e a configuração deste workspace; não apaga arquivos do plugin. Outras configurações de workspace podem ficar órfãs e continuam diagnosticáveis. **Esquecer configuração órfã** limpa a referência local quando o registro não existe. Essas operações não encerram runtimes de outros processos/workspaces já iniciados; os snapshots continuam tendo seu ciclo de vida existente.

Catálogo e grants são compartilhados apenas dentro do mesmo AppPaths/home. O Workbench gerenciado usa seu próprio home, que pode ser diferente daquele de uma execução independente da CLI.

## Exemplo e protocolo

Há um exemplo funcional em [examples/extensions/demo_extension](../examples/extensions/demo_extension/manifest.json). Ele é uma ferramenta de eco, não um scanner científico. Para experimentar sem alterações no projeto, registre esse manifest e use um pedido explícito para a ferramenta `demo_tool`.

Um manifest segue esta estrutura:

```json
{
  "id": "security.scanner",
  "version": "1.0.0",
  "protocol_version": "1.0",
  "transport": "stdio",
  "entrypoint": ["${python}", "${extension_dir}/scanner.py"],
  "timeout_seconds": 30,
  "tools": [{
    "name": "security_scan",
    "description": "Analisa arquivos e devolve findings estruturados.",
    "schema": {
      "type": "object",
      "properties": {"path": {"type": "string"}},
      "required": ["path"],
      "additionalProperties": false
    },
    "capabilities": ["read", "process"],
    "cost": 1
  }]
}
```

O programa é iniciado por invocação, com cwd no workspace. Ele lê o pedido JSON por stdin e escreve exatamente uma linha JSON de resposta em stdout. Logs operacionais devem usar stderr, respeitando os limites de saída. Os placeholders suportados são `${python}` e `${extension_dir}`; não há expansão arbitrária de ambiente.

Pedido:

```json
{"tool":"security_scan","args":{"path":"src"},"invocation_id":"ID-FORNECIDO-PELO-HOST"}
```

Resposta recomendada:

```json
{"invocation_id":"ID-FORNECIDO-PELO-HOST","status":"succeeded","data":{"complete":true,"findings":[]},"message":"Análise concluída."}
```

O ID precisa ser devolvido exatamente. Declare status explícito `succeeded` ou `failed`; o protocolo histórico conserva a compatibilidade que assume succeeded quando status é omitido. O parser rejeita chaves duplicadas, NaN, Infinity e números que transbordam para infinito, além de respostas inválidas, excessivas ou com ID divergente.

O campo data é JSON genérico; o desenvolvedor deve definir e validar seu contrato de domínio. Para o TCC, recomendo findings com ID de regra, CWE quando aplicável, arquivo/posição, evidência, severidade, versão do motor e estado de completude. Resposta de transporte válida não comprova eficácia científica da análise.

## Garantias e limites

O gateway aplica os controles existentes antes do adapter. O escopo opcional por ID também é aplicado no planejamento e na execução. O produto fixa os fingerprints revisados no bootstrap do runtime; plugin indisponível ou manifest substituído não é aceito silenciosamente como a seleção anterior.

A ausência do novo escopo preserva a semântica anterior da API/CLI, baseada em capabilities. Campos novos são nomeados e opcionais. No Workbench, o escopo por ID é criado somente por ação explícita do usuário. Builtins conservam suas políticas e os demais guards.

O fingerprint verifica o manifest, não assinatura de autor, executável ou dependências. Capabilities e cwd não confinam o processo no sistema operacional. Um plugin iniciado pode acessar o que sua identidade de processo conseguir; por isso esta entrega exige plugins de confiança. O ambiente é reduzido e há timeout/limites/cleanup, mas isso não equivale a isolamento de filesystem e rede.

O motor do TCC pode ser externo e integrar-se por esse protocolo. A autorização e os guardas mínimos permanecem no host. Esta entrega não transforma uma tool consultiva em hook obrigatório de política nem implementa o método científico do ScannerCore.
