# Sessão compartilhada entre app e CLI

O app WebView2, `llm-agent`, `llm-agent chat` e `llm-agent local` usam
`InteractiveSession`. As interfaces apresentam resultados e encaminham ações;
admissão, authority, aprovação, consultas e estado pertencem ao núcleo.

| Jornada | App WebView2 | Terminal compartilhado |
| --- | --- | --- |
| Início sem LLM | Sim | Sim |
| Ativar/desativar | Botão explícito | `/llm on/off` |
| Conectividade sem geração | Testar conexão | `/llm test` |
| Comando local pontual | `/exec` ou texto no modo local; confirmação nativa no Workbench | `/exec` ou texto no modo local |
| Descoberta | Paleta do catálogo | `/commands`, completion/F2 |
| Projeto/perfil | Navegação da sessão | `/workspace`, `/model select` |
| Streaming | Polling com drenagem durante espera | Pump no prompt, sem segundo worker |
| Aprovação | Cartão com identidade da solicitação | `/attention`, `/attention details`, `/attention approve/deny` |
| Cancelamento | Botão | `/cancel` ou Ctrl+C |
| Mensagens durante tarefa | Fila de pedidos | Guardadas na mesma fila; envio explícito com `/pending send ID` |
| Plugins | Gerenciador e revisão do processo | Administração por `llm-agent tools` fora da sessão |
| Baterias | Painel Testes e processo independente opcional | `llm-agent queue`, com `--detach` opcional |
| Recuperação | Estado e evidências persistidos | Mesmos serviços de tarefa/fila e evidências |

A execução de comando local inicia um shell novo por envio, com cwd no workspace,
e usa a conta do sistema sem sandbox. O estado de `cd` não persiste. A confirmação
do app mostra a linha antes de executar; a CLI executa o que a pessoa digitou.

A apresentação e alguns atalhos diferem. Uma bateria independente não é uma
tarefa da janela: fechar o app não a cancela. Abra Testes novamente ou consulte
`queue status`; use pausa/parada da fila. Após queda do worker, o lock é liberado
pelo sistema e a fila identifica a interrupção; não repete automaticamente a
tarefa interrompida.

## Compatibilidade

`llm-agent chat --legacy-session` mantém o chat anterior para migração gradual.
Nesse caminho, bootstrap guiado, renderização e gerenciamento da interação ainda
são próprios da CLI. Os testes de compatibilidade continuam verificando esse
comportamento. Ele não é a entrada padrão.

O Workbench Tk permanece experimental e não é o entry point instalado. Seu
lifecycle não é usado como referência de paridade com o app WebView2. Uma futura
retirada precisa respeitar consumidores técnicos e não deve apagar protótipos.

A migração não transforma app ou CLI em um shell geral: eles oferecem operações
do produto, com efeito, resultado e permissões explícitos.
