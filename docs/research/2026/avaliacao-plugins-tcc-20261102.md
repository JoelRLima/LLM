# Avaliação de plugins e recorte do TCC — 02/11/2026

Os artefatos locais citados neste documento não são versionados nem publicados com o repositório. Seus caminhos são referências da investigação original e não comprovam a validação do candidato atual.

> Atualização posterior em 02/11/2026: o ciclo de administração visual e autorização por ID foi implementado, e a prova do launcher foi corrigida. Esta avaliação registra o estado anterior à [entrega da plataforma de plugins locais](../../history/2026/changes/plataforma-plugins-20261102.md). Os limites de sandbox e integração científica continuam explícitos.

O usuário esclareceu que o foco de segurança combina análise de vulnerabilidades e proteção da execução do agente. A avaliação examinou código, documentação CURRENT, configuração local da instalação gerenciada e testes existentes. Não modifica o runtime, não registra plugins nem concede permissões.

## Parecer

A especialização em segurança se encaixa bem em uma extension externa. O núcleo precisa conservar as garantias mínimas de autorização, limites, validação e correlação de execução. Para o foco misto, recomendo um motor de análise independente e um plugin adaptador, complementados por um ponto de integração de política cuja decisão seja aplicada pelo host.

Hoje existe uma base funcional de plugins de ferramentas, com controles de admissão. Não existe uma plataforma geral de plugins capazes de interceptar obrigatoriamente todas as ações ou substituir a política do gateway. Essa segunda função exigiria um contrato novo, explícito e confiável; não se obtém apenas registrando mais uma tool para o LLM escolher.

A documentação CURRENT já reserva essa separação: o scanner interno consolida achados, enquanto um ScannerCore científico externo deve integrar-se pela fronteira de extension. Ver [security.md](../../agent/security.md).

## O que já existe

- Manifest estrito com ID, versão, protocolo próprio 1.0, entrypoint, timeout e descritores de tools.
- Transporte stdio com pedido contendo tool, args e invocation_id e resposta correlacionada. Não é suporte MCP demonstrado: stdio é um transporte, não a implementação do protocolo MCP.
- Catálogo global separado de habilitação e grants por workspace.
- Fingerprint dos bytes do manifest e bloqueio de alterações detectadas na materialização.
- Snapshots imutáveis, registro congelado, tratamento de colisões e bootstrap sem iniciar processos das extensions.
- Gateway com binding, eligibility, autoridade de aplicação/tarefa, capacidades, schema e approval antes do adapter.
- Ambiente de processo reduzido, limites de stdout/stderr, timeout e mecanismos de cancelamento/cleanup.
- CLI moderna `llm-agent extensions` e um exemplo stdio executável. A superfície `tools` legada administra outro registry e não deve ser confundida com o fluxo moderno.

A infraestrutura é real, mas a adoção local ainda é inicial: no home da instalação gerenciada usada pelo launcher e neste workspace, o catálogo moderno está ausente e não há extensions registradas ou seleções persistidas. Essa observação não cobre outros homes, instalações legadas ou workspaces.

No lançamento padrão do Workbench, TaskExecutionStart não recebe startup_capabilities, cujo padrão é None. Portanto, registrar/habilitar uma extension não basta para torná-la utilizável pelo modelo nessa sessão. A API aceita autoridade explícita; a CLI headless oferece --task-authority. Falta uma experiência apropriada para administrar e autorizar plugins no desktop.

## Limites relevantes para segurança

1. **Grants não são sandbox do sistema operacional.** O gateway controla a admissão da chamada. Depois que um programa externo é iniciado, cwd e capacidades declaradas não confinam seu acesso real a arquivos e rede. Timeout, ambiente reduzido e Windows Job Objects tratam partes do ciclo de vida, não equivalem a isolamento de código hostil.
2. **Fingerprint de manifest não fixa o programa inteiro.** O código referenciado, executáveis e dependências podem mudar sem alterar o JSON do manifest. Não foi identificada nessa fronteira uma verificação de integridade do pacote completo.
3. **Tools não são hooks obrigatórios de política.** Um security_check escolhido pelo LLM pode ser omitido. Uma política obrigatória deve ser chamada pelo host no caminho de execução, com ordem, timeout e tratamento de erro definidos.
4. **O contrato de findings científicos precisa ser definido.** O transporte retorna data genérico. Para o TCC, é necessário validar conteúdo de domínio: regra/CWE, localização, evidência, severidade, limitações, versões e completude da análise.
5. **Resultado de plugin não cria autoridade.** Texto do scanner deve continuar sendo dado não confiável. Um achado não pode autorizar escrita, instalação, rede ou alteração de permissões.
6. **Experiência de uso e testes precisam de acabamento.** Há configuração por CLI e fluxos distintos legado/moderno. A seleção de testes revelou uma referência antiga no teste do launcher Windows.

O risco de subprocessos stdio sem sandbox também está explícito no [modelo de segurança do MCP](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/SECURITY.md). Essa referência serve de comparação de fronteira de processo; não significa que este agent implemente MCP. A [OWASP](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/) recomenda privilégios mínimos e autorização independente do LLM. A [cheat sheet de MCP da OWASP](https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html) distingue integridade de metadados de integridade do código e recomenda isolamento de filesystem/rede.

## Divisão recomendada para o TCC

| Componente | Responsabilidade |
| --- | --- |
| Núcleo do agent | Mediar todas as invocações, limitar recursos, aplicar autoridade e approval, preservar registros e tratar saídas como dados. |
| ScannerCore externo | Implementar e versionar o método científico de análise de código/dependências. Deve poder ser testado sem o agente. |
| Plugin security.scanner | Adaptar solicitações explícitas para o motor e devolver findings estruturados, com evidências e estado da análise. |
| Avaliador de política opcional, proposto | Avaliar ações candidatas e acrescentar restrições. Se obrigatório na configuração, o host deve executá-lo e aplicar sua decisão. Não concede capacidades nem substitui checks mínimos. |
| Workbench | Permitir administrar origem, versão, permissões, estado e resultados com clareza. |

Uma primeira ferramenta pode ser `security_scan`, com escopo e modo de análise fechados. Inicialmente ela deve analisar sem corrigir arquivos. A execução stdio requer process e as capacidades realmente necessárias, como read. Se for exigida ausência de acesso à rede ou escrita no projeto, essas restrições precisam ser impostas também pelo ambiente de execução, não somente declaradas no manifest.

Uma ferramenta ou serviço de política como `security_review_action` é uma proposta posterior. A decisão deve ser estruturada e vinculada à ação exata, ao alvo, ao workspace e à versão da política. Uma resposta favorável só deixa prosseguir verificações já exigidas; não cria permissões. Se uma política configurada como obrigatória estiver indisponível ou responder de forma inválida, a ação protegida não deve ser liberada silenciosamente. Esse ponto não está implementado hoje como plugin de política.

O modelo pode selecionar análises e explicar resultados. Findings, arquivos do projeto e saídas externas não devem alterar as regras que autorizam ações. Um plugin de análise de terceiros não deve receber, por essa mesma instalação, o direito de autorizar sua própria execução.

## Recorte científico sugerido

“Plugin auditável de análise de segurança com execução controlada em um agente de IA” é um recorte possível. Transformar a função em plugin é uma decisão de engenharia; a contribuição científica precisa estar no método, nas garantias investigadas e na avaliação.

Para manter o foco misto viável, escolher uma família de vulnerabilidades e uma classe de ações protegidas. Comparar separadamente:

- Qualidade da análise: precisão, recall, falsos positivos, localização e suporte por evidência, sobre casos rotulados.
- Controle da execução: efeitos não autorizados, tentativas bloqueadas, falsos bloqueios, falhas/timeout do plugin e preservação da utilidade.
- Custo: chamadas ao modelo, tokens observados, tempo e versões exatas dos componentes.

Uma evolução experimental pode comparar o núcleo atual, o núcleo com scanner consultivo e, quando implementado, o núcleo com integração obrigatória de política. O scanner consultivo não deve ser apresentado como garantia de mediação de todas as ações. Simulações validam o harness, mas não comprovam qualidade de modelos reais ou eficácia universal de segurança.

## Verificação desta rodada

Foram executados testes existentes de parser de manifest, catálogo, grants, resolução, materialização, bootstrap, autoridade, gateway, adapter stdio, exemplo executável e integração de workspaces.

Resultado: **203 aprovados, 2 ignorados e 1 falha**. JUnit (artefato local: `reports/plugins-tcc-20261102/tests.xml`).

A falha está em `test_windows_launcher_waits_for_job_association_before_immediate_extension_sentinel`, que tenta acessar `llm_agent.execution.command.write_stdin`, atributo que não existe mais. O código atual usa `start_stdin_writer`. O teste falha antes de exercitar a ordem de execução. A leitura do código mostra associação ao Job Object antes do início da escrita em stdin, mas isso não substitui a prova dinâmica que esse teste deveria fornecer. Não se conclui daí que houve uma fuga nem que a suíte está completamente aprovada.

Não foram alterados testes, plugins, grants ou código de produção. Não houve inferência real. Os processos externos iniciados pelos testes pertencem às fixtures e ao exemplo de demonstração, em diretórios temporários.

## Prioridade de implementação

1. Atualizar a prova do launcher Windows e fechar a suíte atual antes de usar esses resultados como evidência do TCC.
2. Definir um threat model e um contrato de findings versionado; integrar um ScannerCore separado pela extension atual.
3. Tornar clara a administração e a autoridade de plugins no Workbench.
4. Implementar isolamento real e identidade do pacote se o threat model incluir plugins hostis ou comprometidos.
5. Acrescentar um contrato de política obrigatório no host apenas se esse for o mecanismo experimental do TCC, preservando os checks mínimos do núcleo.
