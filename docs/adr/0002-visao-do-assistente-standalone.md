# ADR 0002: visão do assistente standalone e fronteiras de extensão

- Status: aceito
- Data: 2026-07-30

## Contexto

O projeto nasceu como um agente local orientado a tarefas de código. Ele já
possui abstração de modelo, planejamento, execução, skills embutidas, memória e
controles de runtime, mas ainda não define um produto standalone nem um
contrato geral para capacidades externas.

O objetivo passa a ser uma plataforma de assistente pessoal completa no sentido
operacional: receber um objetivo, obter contexto, planejar, agir por meios
internos ou externos, observar e validar os efeitos, responder e registrar o
resultado. "Completa" não significa incluir toda modalidade ou integração no
núcleo; significa fechar esse ciclo de forma extensível, verificável e segura.

O repositório do TCC será um consumidor dessa infraestrutura. Ele precisa
continuar cientificamente reproduzível sem o assistente e, ao mesmo tempo,
oferecer suas capacidades à plataforma sem compartilhar detalhes internos.

## Decisão

### Identidade da plataforma

O projeto será uma aplicação standalone com um núcleo pequeno e extensível. O
assistente é o sistema formado por perfil, políticas, memória, capacidades,
configuração e ciclo de tarefas. O modelo é uma dependência substituível desse
sistema, não sua identidade nem sua fonte exclusiva de decisões.

A metáfora de cérebro, sentidos e membros serve para comunicar a visão, não
para nomear interfaces de código. Os contratos usam termos técnicos explícitos.

### Vocabulário canônico

| Termo | Definição |
| :--- | :--- |
| **assistant** | Entidade conceitual formada pela aplicação, políticas, memória, capabilities e configuração, que coordena modelos e ferramentas. |
| **AgentApplication** | Root de composição e controlador do lifecycle. Monta paths, configuração, sessão, registry e o orquestrador. |
| **Orchestrator** | Componente responsável pela coordenação de tarefas, planejamento, execução, checkpoint, revisão e relatório. |
| **model** | Modelo de inferência que produz respostas ou propostas a partir de uma entrada. Não possui autoridade para executar efeitos. |
| **provider** | Adapter que disponibiliza um model por um contrato estável, independentemente do backend. |
| **skill** | Procedimento ou capacidade composta do assistant. Pode coordenar raciocínio e uma ou mais tools; não implica processo externo. |
| **tool** | Operação canônica e diretamente invocável, com entrada, saída, efeitos e capabilities declarados. É a unidade de autorização e execução. |
| **extension** | Pacote ou sistema externo que oferece tools por um protocolo suportado. Não é carregada como módulo interno. |
| **adapter** | Implementação interna que traduz uma fonte de tools ou de modelo para contratos canônicos. |
| **transport** | Mecanismo de comunicação com uma extension, como subprocesso por stdio. |
| **workspace** | Diretório-alvo sobre o qual a tarefa atua. |
| **capability** | Categoria de autoridade necessária para determinada operação. |
| **grant** | Concessão explícita de uma capability a uma extension ou contexto. O código atual ainda não possui esse conceito de domínio implementado. |
| **approval** | Decisão relativa a uma ação concreta. Não deve ser tratada como sinônimo de grant. |
| **artifact** | Resultado material produzido por uma execução. |
| **diagnostic** | Informação estruturada sobre condição, falha ou observação. Atualmente não existe como contrato unificado em `ToolResult`. |
| **memory** | Informação persistida para uso posterior pelo assistant. |
| **checkpoint** | Snapshot persistido necessário para retomar uma tarefa. |
| **operational state** | Estado transitório e persistível da execução, separado semanticamente da memória. |

As definições acima refletem o estado atual do código. Em particular, `grant`
ainda não é um objeto de domínio plenamente implementado e `diagnostic` ainda
não integra o contrato canônico de tools.

`skill` permanece como conceito de composição do assistente. `tool` será o
contrato comum para operações embutidas e externas; uma skill embutida atual
poderá ser exposta por um adapter sem reescrita imediata.

### Fronteiras e responsabilidades

- O núcleo possui o ciclo de tarefas, os contratos de modelo e tools, o
  registro de capacidades, autorização, contexto, memória, cancelamento,
  eventos e artifacts.
- Interfaces como CLI ou API apenas traduzem entrada, saída e consentimento;
  não contêm regras exclusivas de execução.
- Providers traduzem o contrato de modelo para um backend. Trocar provider ou
  model não altera políticas, memória ou identidade do assistant.
- Extensions implementam capacidades específicas e se comunicam somente por
  contrato público e versionado. Elas não importam internals da plataforma.
- O workspace é fornecido à tarefa e limita o escopo de recursos. Instalação,
  configuração global, estado, cache e artifacts possuem localizações
  distintas.
- Planejamento e sugestão não concedem autoridade. Todo efeito passa pelo mesmo
  caminho de validação, autorização, execução e auditoria.

O núcleo deve continuar pequeno, mas o produto não precisa ser mínimo: novas
interfaces, providers e extensions se conectam pelas portas acima sem ampliar o
conjunto de responsabilidades do orquestrador.

### Modelo de confiança

Adotamos privilégio mínimo e negação por padrão:

1. saída de model é proposta não confiável;
2. manifesto e saída de extension são dados não confiáveis;
3. descobrir ou instalar uma extension não a habilita, não concede
   capabilities e não autoriza uma invocação;
4. implementação conhecida ou embutida não substitui autorização para o efeito
   concreto;
5. a autoridade efetiva é a interseção entre capability declarada, concessão à
   extension, autoridade da tarefa e recurso solicitado;
6. dados de tool, model, workspace e memória carregam origem; conteúdo externo
   não se transforma em instrução privilegiada;
7. extensions não escrevem diretamente na memória do assistant: podem propor
   fatos, que passam por validação, política de retenção e, quando necessário,
   confirmação;
8. secrets não são incluídos em contexto ou ambiente de processo por padrão;
9. falha de schema, protocolo, versão ou autorização encerra a operação de
   forma fechada e auditável.

Isolamento em subprocesso reduz acoplamento e contém o ciclo de vida, mas não é
considerado uma sandbox de segurança forte. Garantias adicionais devem ser
declaradas conforme o sistema operacional e o transport utilizado.

### Relação com o TCC

O TCC é independente em duas direções:

- **independência científica:** possui sua própria CLI ou executor de
  experimentos, configuração, dependências, testes, versões e resultados
  reproduzíveis, sem depender deste repositório;
- **integração operacional:** oferece tools por um adapter fino que implementa o
  mesmo protocolo público utilizado por qualquer outra extension.

A plataforma não conhece conceitos internos do TCC nem contém condicionais
específicas para ele. O TCC não importa classes internas da plataforma. Remover
o TCC remove apenas suas tools; o assistant permanece funcional. Experimentos
acadêmicos chamam diretamente o TCC, evitando que decisões do assistant alterem
o objeto medido.

### Definição verificável de standalone v1

A versão v1 será considerada standalone quando todos estes critérios forem
atendidos por testes ou procedimentos reproduzíveis:

1. o pacote instalado executa a partir de um diretório arbitrário, sem checkout
   do código-fonte ou IDE;
2. configuração, estado, cache, secrets referenciados e artifacts não dependem
   do diretório atual nem são escritos no pacote instalado;
3. toda tarefa recebe um workspace explícito e não acessa recursos fora dele
   sem capability e concessão específicas;
4. pelo menos dois providers ou um provider real e um fake de conformidade
   demonstram que os casos de uso não dependem de um model específico;
5. tools embutidas e externas atravessam contratos comuns de descrição,
   invocação, resultado, erro, timeout, cancelamento e auditoria;
6. uma extension de referência executa fora do processo, sem imports cruzados,
   e incompatibilidades de protocolo falham de forma fechada;
7. operações com efeito são negadas sem autoridade e podem exigir aprovação
   segundo o nível de autonomia;
8. uma tarefa completa percorre entrada, contexto, planejamento, autorização,
   execução, verificação, resposta e persistência controlada;
9. o trace permite identificar model/provider, tools e versões, autorizações,
   artifacts, efeitos observados e motivo do estado terminal;
10. o perfil de baixo consumo limita inferência concorrente, carrega
    capacidades sob demanda e permanece utilizável em uma GPU com 8 GB de VRAM.

Esses critérios definem o alvo da v1; a aceitação deste ADR não afirma que o
estado atual já os satisfaz.

### Não objetivos da v1

Não fazem parte do gate inicial:

- GUI, voz ou visão nativas;
- marketplace, descoberta remota ou atualização automática de extensions;
- instalação automática de código externo;
- execução distribuída, multiusuário ou serviço público;
- autonomia contínua em background;
- autoalteração irrestrita do assistant;
- sandbox forte uniforme entre sistemas operacionais;
- transports remotos como requisito obrigatório;
- embutir um model ou seus pesos no artefato da aplicação;
- suportar toda ferramenta ou integração imaginável.

Essas capacidades poderão ser adicionadas por interfaces ou extensions após a
estabilização do núcleo.

## Consequências

- A evolução prioriza standalone, contratos e segurança antes de ampliar o
  catálogo de skills.
- O catálogo embutido atual precisa migrar por compatibilidade para o contrato
  comum de tools, sem ruptura desnecessária.
- Extensibilidade ganha custos explícitos de versionamento, conformance,
  lifecycle e auditoria.
- Uma falha em extension ou model não deve comprometer a identidade, a memória
  ou a inicialização básica do assistant.
- O TCC poderá evoluir e ser validado separadamente, reduzindo acoplamento
  científico e operacional.
- A arquitetura e as jornadas que materializam a decisão estão descritas em
  [Plataforma standalone](../plataforma-standalone.md).
