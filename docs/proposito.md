# Propósito do Workbench e do LLM Agent

**Status: CURRENT — referência normativa de produto.**  
**Atualizado em:** 02/11/2026.

Este documento define o Workbench como ambiente de projeto e o LLM Agent como uma de suas capacidades. Ele orienta prioridades de produto; não substitui contratos de arquitetura, documentação de uso nem evidência de que uma capacidade já esteja implementada.

## Propósito

O Workbench é um ambiente local de trabalho para projetos de software. Arquivos e Git, análise estática, baterias de testes, execuções, resultados e plugins formam as ferramentas do ambiente. O LLM Agent é uma capacidade opcional para conversa e tarefas agênticas.

O LLM Agent ajuda uma pessoa a entender e executar tarefas de desenvolvimento em um projeto escolhido. Ele recebe um objetivo, consulta o contexto permitido, planeja o trabalho, executa operações dentro das permissões da sessão, verifica os resultados disponíveis e apresenta o que fez e o que não conseguiu confirmar.

No Workbench, a LLM é opcional para o uso básico. Sem ativá-la, a pessoa ainda pode navegar entre projetos, consultar arquivos e Git, executar análises estáticas de código, preparar baterias de teste, conferir critérios e inspecionar resultados. Com a LLM ativada, pode pedir análise conversacional e trabalho agêntico. A CLI mantém seu fluxo de configuração e interação próprio, usando os mesmos serviços e regras de domínio. A interface explica quando uma ação exige modelo e não deve confundir conectividade, execução e sucesso verificado.

CLI e Workbench apresentam o mesmo núcleo de casos de uso. A interface adapta entrada, saída e consentimento; não cria uma segunda regra de negócio nem outro executor.

## Para quem e para quais tarefas

O produto atende pessoas que desenvolvem ou estudam software e precisam de ajuda em um projeto local ou workspace explicitamente escolhido. A prioridade é tornar tarefas como estas mais simples de iniciar, acompanhar e revisar:

- entender um repositório e localizar informação;
- analisar e revisar código;
- propor e aplicar mudanças com validação e as aprovações exigidas;
- executar tarefas agênticas ou séries de experimentos autorizadas;
- conferir resultados com evidências observáveis e critérios externos quando disponíveis.

O TCC de segurança pode integrar o Agent como plugin ou extension, com fronteira e protocolo próprios. O propósito do Agent não depende de detalhes internos do TCC; experimentos acadêmicos devem permanecer reproduzíveis fora do Agent.

## Princípios para decisões de produto

Uma mudança serve ao propósito quando melhora um resultado concreto para a pessoa e respeita estes princípios:

1. **Uso intuitivo:** tarefas comuns são descobríveis, os próximos passos ficam claros e erros indicam como prosseguir.
2. **Uso local útil:** capacidades que não precisam de inferência continuam acessíveis sem configuração ou chamadas à LLM, quando tecnicamente possível.
3. **Núcleo compartilhado:** CLI e Workbench delegam regras e execução aos mesmos casos de uso do Agent.
4. **Ação explícita:** o produto distingue preparação, execução, efeitos, autorização e verificação. Ativar a LLM ou começar uma bateria não oculta o custo ou o efeito relevante.
5. **Resultado verificável:** o Agent separa o que o runtime declarou do que foi confirmado por evidências. Se a verificação não existe ou não concluiu, informa essa limitação.
6. **Transparência técnica:** estados, limites, permissões, falhas e evidências ficam disponíveis sem obrigar a pessoa a entender a arquitetura interna.
7. **Modularidade:** integrações específicas entram por fronteiras explícitas; dependências e estado compartilhado precisam ter um motivo concreto.
8. **Segurança proporcional:** sugestões do modelo não são autoridade. Efeitos respeitam o workspace, as permissões, as validações e as aprovações existentes.

## Fora do propósito central

O Agent não pretende ser uma plataforma distribuída de multiagentes, um serviço contínuo que age sem solicitação ou um sandbox universal. O uso local inclui um executor de comandos pontuais escrito pela pessoa, com o shell do sistema e os privilégios da conta atual; isso permite tarefas comuns sem LLM, mas não substitui um terminal interativo persistente nem oferece isolamento. Plugins e extensions podem ampliar capacidades sem transformar cada integração em obrigação do núcleo. A existência de uma ideia neste documento também não prova que ela esteja implementada ou pronta para produção.

## Perguntas para avaliar uma mudança

Antes de incluir uma capacidade no produto ou no núcleo, responda:

- Qual tarefa ou dificuldade concreta da pessoa melhora?
- O que a pessoa verá e como saberá o próximo passo?
- A capacidade pode funcionar sem LLM? Se não, isso está explícito antes da execução?
- CLI e Workbench usam o mesmo caso de uso?
- Como autorização, falha e resultado serão apresentados?
- O resultado pode ser confirmado por evidência? Se não, o produto declara que está sem verificação?
- A capacidade é específica de uma integração ou domínio e deveria ser um plugin?
- O ganho justifica o acoplamento, estado e custo de manutenção que introduz?

Uma resposta negativa não encerra automaticamente a proposta. Ela indica qual evidência, fronteira ou explicação ainda precisa ser resolvida. Mudanças pequenas podem ser avaliadas de forma proporcional; mudanças de segurança, autoridade ou efeitos exigem justificativa mais forte.

## Relação com a documentação vigente

Este documento define propósito e critérios de produto. [Plataforma standalone](plataforma-standalone.md) descreve capacidades e limites CURRENT; [documentação técnica](README.md) aponta os contratos vigentes. O [ADR 0002](adr/0002-visao-do-assistente-standalone.md) registra a visão e as decisões originais de julho de 2026, incluindo contexto e metas históricas; leia-o como registro de decisão, não como lista atual de escopo.
