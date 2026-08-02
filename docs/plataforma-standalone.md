# Plataforma standalone

Este guia materializa o alvo aprovado no
[ADR 0002](adr/0002-visao-do-assistente-standalone.md). Ele descreve a
arquitetura e as jornadas usadas como critério de evolução. O bootstrap
entregue nas fases 0 e 1 é detalhado no
[ADR 0003](adr/0003-bootstrap-paths-e-ciclo-de-vida-standalone.md); os contratos
de tools e extensions mostrados abaixo estão em implementação incremental;
o estado factual abaixo prevalece sobre o diagrama.

## Estado de implementação

| Parte | Estado | Evidência principal |
| :--- | :--- | :--- |
| visão, vocabulário, trust model e gates | entregue na fase 0 | ADR 0002 e baseline executável hermético |
| paths de aplicação e isolamento por workspace | entregue na fase 1 | `AppPaths`, `WorkspaceContext` e `WorkspacePaths` |
| configuração versionada e migração explícita | entregue na fase 1 | `ConfigRepository` e comandos `config`/`state` |
| composição independente de UI e modo headless | entregue na fase 1 | `AgentApplication` e `llm-agent run` |
| consentimento local para escrita e validação por subprocesso | entregue na fase 1 | `ApprovalPort`, bloqueio headless e `run --yes` |
| diagnóstico offline e aceitação do wheel | entregue na fase 1 | `llm-agent doctor` e gate de pacote instalado |
| contrato comum de tools e adapters embutidos | entregue/parcial | `agent/tools/contracts.py`, `ToolRegistry`, adapters e suíte atual |
| extensions externas e transport stdio | entregue/parcial | `ExtensionRegistry`, `StdioToolAdapter`, manifest/protocolo `1.0` com `invocation_id` estrito; cancelamento cooperativo ainda limitado |
| autorização por capability e aprovação | entregue/parcial | `AuthorizationContext`, `ApprovalPort` e gateway; grants persistentes completos ainda pendentes |
| conformance ponta a ponta e retomada reautorizada | pendente | acompanhar o runbook das fases 0–4 |

## Arquitetura-alvo

```text
CLI / modo headless / futura API
                 |
                 v
          AgentApplication
                 |
                 v
        ciclo único da tarefa
     contexto -> plano -> autorização
                 |
                 v
        ToolInvocationGateway
          |             |
          v             v
   tools embutidas   extensions
        adapter       transport
          |             |
          +------v------+
          resultado, artifacts,
          eventos e diagnósticos
                 |
          verificação -> resposta
                 |
       persistência controlada
```

O núcleo coordena e aplica política; adapters traduzem. Nenhuma interface,
provider ou extension cria um caminho alternativo para produzir efeitos.

## Invariantes

- A identidade do assistant independe do model selecionado.
- Ausência de model, provider ou extension produz indisponibilidade explícita,
  não uma inicialização parcialmente quebrada.
- O workspace é um valor explícito e diferente do diretório de instalação.
- Planejar, autorizar e executar são operações distintas.
- Toda operação com efeito é observável, cancelável quando suportado e associada
  a um contexto de tarefa.
- Resultados externos são evidência a validar, nunca comandos privilegiados.
- Skills conhecidas fazem seleção e composição determinísticas sempre que
  possível; o model recebe apenas alternativas válidas e relevantes.
- Em perfil de 8 GB de VRAM existe uma única inferência ativa e capacidades
  pesadas são carregadas sob demanda.

## Jornadas de referência

Cada jornada deve ser automatizada com doubles herméticos antes de integrar
serviços reais.

### 1. Inicialização fora do checkout

**Dado** um pacote instalado em ambiente limpo, **quando** o usuário inicia o
assistant em outro diretório, **então** configuração e estado são encontrados
em diretórios próprios e nenhum arquivo é criado no pacote instalado.

### 2. Análise somente leitura

**Dado** um workspace explícito e autoridade de leitura, **quando** o usuário
pede uma análise, **então** o assistant seleciona tools compatíveis, não produz
efeitos de escrita e retorna artifacts com origem e trace.

### 3. Alteração com consentimento

**Dado** o nível de autonomia `propose`, **quando** uma tarefa requer escrita,
**então** o assistant apresenta a mudança e seu impacto, aguarda aprovação,
aplica somente o autorizado e valida ou reverte o efeito.

O consentimento local para as ferramentas embutidas já existe: o chat pode
perguntar ao usuário, enquanto headless retorna `blocked` ou recebe autoridade
explícita com `run --yes`. Níveis persistentes de autonomia e a política comum
para extensions pertencem às fases seguintes.

### 4. Negação de capability

**Dado** uma tool que solicita rede ou escrita sem concessão, **quando** ela é
selecionada, **então** a operação termina como não autorizada antes do efeito e
o trace identifica capability e recurso negados.

A política atual de personas e o consentimento de escrita cobrem apenas parte
dessa jornada. A decisão uniforme por capability e recurso, aplicada também a
tools externas, ainda não foi entregue.

### 5. Extension independente

**Dado** uma aplicação externa compatível, **quando** sua tool é invocada,
**então** plataforma e extension trocam mensagens pelo protocolo público, sem
imports cruzados, com timeout, cancelamento, limite de saída e resultado
estruturado.

### 6. TCC como instrumento

**Dado** o repositório do TCC instalado e habilitado como extension, **quando**
o assistant solicita uma análise acadêmica, **então** ele usa a mesma fronteira
das demais tools, registra versão e evidências e trata o retorno como não
confiável. A mesma análise continua executável diretamente no TCC.

### 7. Falha e retomada

**Dado** uma tarefa interrompida, **quando** o assistant é reiniciado, **então**
ele distingue efeitos confirmados de operações incertas, não repete
silenciosamente uma ação e oferece retomada ou encerramento auditável.

### 8. Troca de model

**Dado** outro provider compatível, **quando** o perfil ativo muda, **então** o
ciclo, as políticas, a memória, os contratos de tools e os critérios de
verificação permanecem iguais.

## Sequência de evolução

1. ~~separar instalação, dados da aplicação, workspace, cache e artifacts;~~
2. ~~criar composição e ciclo de vida únicos para CLI e modo headless;~~
3. estabilizar contratos comuns de tools e adaptar as skills atuais;
4. introduzir autorização por capability e recurso;
5. implementar e testar o primeiro transport externo;
6. completar memória, níveis de autonomia, trace e verificação;
7. integrar o TCC e uma segunda extension pelo mesmo protocolo;
8. endurecer segurança, conformance, empacotamento e release.

Uma fase só encerra quando suas jornadas relevantes possuem evidência
reproduzível. Documentação de estado atual deve continuar distinguindo
claramente o que já existe do que pertence a este alvo.
