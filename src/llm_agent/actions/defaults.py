"""Closed W19 product action catalog."""

from __future__ import annotations

from llm_agent.actions.catalog import ActionCatalog
from llm_agent.actions.models import ActionCategory as C
from llm_agent.actions.models import ActionDefinition as D
from llm_agent.actions.models import ActionPayloadKind as P
from llm_agent.actions.models import ActionScope as S
from llm_agent.actions.models import ActionTarget as T


def _d(
    action_id: str,
    title: str,
    category: C,
    scope: S,
    target: T,
    payload_kind: P,
    description: str,
    payload_hint: str | None = None,
    keywords: tuple[str, ...] = (),
    discovery_examples: tuple[str, ...] = (),
) -> D:
    return D(action_id, title, description, category, scope, target, payload_kind, payload_hint, keywords, discovery_examples)


DEFAULT_ACTION_CATALOG = ActionCatalog(
    (
        _d("discovery.help", "Ajuda", C.DISCOVERY, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "Conheça os recursos principais e como conversar com o Agent."),
        _d("discovery.commands", "Comandos", C.DISCOVERY, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.OPTIONAL_TEXT, "Busque comandos e recursos; a seleção prepara um rascunho para revisão.", "query", ("commands", "discover", "search"), ("/commands", "/commands query")),
        _d("run.status", "Estado da sessão", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "Consulte modelo, workspace, modo e estado da sessão."),
        _d("run.where", "Atividade atual", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "Acompanhe a atividade da tarefa atual."),
        _d("run.timeline", "Linha do tempo", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "Veja os acontecimentos recentes da tarefa."),
        _d("run.details", "Detalhes técnicos", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "Consulte os detalhes técnicos da execução."),
        _d("run.cancel", "Cancelar tarefa", C.RUN, S.PRODUCT, T.APPLICATION_CONTROL, P.NONE, "Solicite o cancelamento da tarefa em andamento."),
        _d("run.pending_list", "Mensagens pendentes", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "Liste as mensagens que aguardam envio."),
        _d("run.pending_send", "Enviar mensagem pendente", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.TYPED, "Envie uma mensagem pendente pelo número.", "pending id"),
        _d("run.pending_edit", "Editar mensagem pendente", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.TYPED, "Carregue uma mensagem pendente para edição.", "pending id"),
        _d("run.pending_discard", "Descartar mensagem pendente", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.TYPED, "Descarte uma mensagem pendente pelo número.", "pending id"),
        _d("run.attention_show", "Aprovação pendente", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "Consulte a solicitação de aprovação atual."),
        _d("run.attention_details", "Detalhes da aprovação", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "Revise os detalhes da solicitação de aprovação atual."),
        _d("run.attention_approve", "Aprovar solicitação", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "Aprove explicitamente a solicitação atual."),
        _d("run.attention_deny", "Negar solicitação", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "Negue explicitamente a solicitação atual."),
        _d("model.show", "Modelo", C.MODEL, S.PRODUCT, T.CONFIGURATION, P.NONE, "Consulte o modelo e o provedor configurados na sessão."),
        _d("model.llm_control", "Ativar LLM", C.MODEL, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.OPTIONAL_TEXT, "Ative ou desative a LLM sem enviar pedidos; confira a conexão explicitamente.", "status|on|off|test", ("llm", "ativar", "desativar", "conexao", "local"), ("/llm status", "/llm on", "/llm off", "/llm test")),
        _d("model.select", "Selecionar modelo", C.MODEL, S.PRODUCT, T.CONFIGURATION, P.OPTIONAL_TEXT, "Selecione um perfil de modelo quando a sessão estiver livre.", "profile"),
        _d("configuration.system_show", "Consultar instruções", C.CONFIGURATION, S.PRODUCT, T.APPLICATION_CONTROL, P.NONE, "Veja as instruções de sistema ativas."),
        _d("configuration.system_set", "Alterar instruções", C.CONFIGURATION, S.PRODUCT, T.APPLICATION_CONTROL, P.OPTIONAL_TEXT, "Defina as instruções de sistema da conversa."),
        _d("configuration.thinking_toggle", "Raciocínio", C.CONFIGURATION, S.PRODUCT, T.APPLICATION_CONTROL, P.OPTIONAL_TEXT, "Ative ou configure o orçamento de raciocínio."),
        _d("history.clear", "Limpar histórico", C.HISTORY, S.PRODUCT, T.APPLICATION_CONTROL, P.NONE, "Limpe o histórico da conversa."),
        _d("history.save", "Salvar histórico", C.HISTORY, S.PRODUCT, T.APPLICATION_CONTROL, P.OPTIONAL_TEXT, "Salve o histórico da conversa."),
        _d("history.load", "Carregar histórico", C.HISTORY, S.PRODUCT, T.APPLICATION_CONTROL, P.OPTIONAL_TEXT, "Carregue um histórico de conversa."),
        _d("configuration.debug_toggle", "Diagnóstico da interface", C.CONFIGURATION, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "Alterne o nível de detalhes de diagnóstico."),
        _d("memory.show", "Memória", C.MEMORY, S.PRODUCT, T.APPLICATION_CONTROL, P.NONE, "Consulte a memória da sessão."),
        _d("memory.remember", "Memorizar", C.MEMORY, S.PRODUCT, T.APPLICATION_CONTROL, P.REQUIRED_TEXT, "Guarde uma chave e um valor na memória.", "key value"),
        _d("memory.forget", "Esquecer", C.MEMORY, S.PRODUCT, T.APPLICATION_CONTROL, P.OPTIONAL_TEXT, "Remova uma chave da memória.", "key"),
        _d("memory.clear", "Limpar memória", C.MEMORY, S.PRODUCT, T.APPLICATION_CONTROL, P.NONE, "Limpe a memória da sessão."),
        _d("memory.save", "Salvar memória", C.MEMORY, S.PRODUCT, T.APPLICATION_CONTROL, P.OPTIONAL_TEXT, "Salve a memória da sessão."),
        _d("memory.load", "Carregar memória", C.MEMORY, S.PRODUCT, T.APPLICATION_CONTROL, P.OPTIONAL_TEXT, "Carregue a memória da sessão."),
        _d("maintenance.doctor", "Diagnóstico", C.MAINTENANCE, S.PRODUCT, T.APPLICATION_CONTROL, P.OPTIONAL_TEXT, "Verifique a configuração e as capacidades locais; --online testa a conexão."),
        _d("query.list_files", "Listar arquivos", C.QUERY, S.PRODUCT, T.WORKSPACE_QUERY, P.OPTIONAL_TEXT, "Liste os arquivos dentro do workspace.", "path"),
        _d("workspace.show", "Workspace", C.WORKSPACE, S.PRODUCT, T.APPLICATION_CONTROL, P.NONE, "Veja a pasta de trabalho ativa."),
        _d("workspace.switch", "Trocar workspace", C.WORKSPACE, S.PRODUCT, T.APPLICATION_CONTROL, P.OPTIONAL_TEXT, "Escolha outra pasta de trabalho quando a sessão estiver livre.", "path"),
        _d("configuration.mode_show", "Modo de operação", C.CONFIGURATION, S.PRODUCT, T.APPLICATION_CONTROL, P.NONE, "Consulte o modo e as permissões da sessão."),
        _d("configuration.mode_set", "Alterar modo", C.CONFIGURATION, S.PRODUCT, T.APPLICATION_CONTROL, P.REQUIRED_TEXT, "Escolha read-only, editor ou full; as autorizações continuam obrigatórias.", "read-only|editor|full"),
        _d("run.retry", "Tentar novamente", C.RUN, S.PRODUCT, T.INTERACTION, P.NONE, "Tente executar novamente a tarefa anterior."),
        _d("inspection.open", "Inspecionar", C.INSPECTION, S.PRODUCT, T.INSPECTION, P.OPTIONAL_TEXT, "Inspecione a execução atual ou uma saída."),
        _d("interaction.agent_submit", "Executar tarefa", C.INTERACTION, S.PRODUCT, T.INTERACTION, P.REQUIRED_TEXT, "Envie um objetivo ao Agent.", "objective"),
        _d("interaction.code_submit", "Trabalhar com código", C.INTERACTION, S.PRODUCT, T.CODE_WORKFLOW, P.REQUIRED_TEXT, "Solicite um trabalho com código; veja detalhes em /code help.", "code request"),
        _d("query.read", "Ler arquivo", C.QUERY, S.PRODUCT, T.WORKSPACE_QUERY, P.REQUIRED_TEXT, "Leia um arquivo dentro do workspace.", "file"),
        _d("query.find", "Buscar texto", C.QUERY, S.PRODUCT, T.WORKSPACE_QUERY, P.REQUIRED_TEXT, "Busque texto nos arquivos do workspace.", "pattern"),
        _d("process.exec", "Executar comando local", C.INTERACTION, S.PRODUCT, T.APPLICATION_CONTROL, P.REQUIRED_TEXT, "Execute um comando escrito por você no shell do sistema, a partir do workspace. Usa a conta atual, não é sandbox e nunca é chamado pelo modelo.", "comando", ("terminal", "shell", "powershell", "cmd", "executar comando"), ("/exec pytest", "/exec Get-ChildItem")),
        _d("query.git_status", "Estado do Git", C.QUERY, S.PRODUCT, T.WORKSPACE_QUERY, P.NONE, "Consulte as alterações no repositório Git."),
        _d("query.git_diff", "Diferenças do Git", C.QUERY, S.PRODUCT, T.WORKSPACE_QUERY, P.OPTIONAL_TEXT, "Veja as diferenças do repositório Git.", "pathspecs"),
        _d("query.web_search", "Pesquisar na web", C.QUERY, S.PRODUCT, T.WEB_SEARCH, P.REQUIRED_TEXT, "Pesquise informações na web.", "query"),
        _d("session.exit", "Sair", C.SESSION, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "Encerre a sessão interativa."),
    )
)


__all__ = ["DEFAULT_ACTION_CATALOG"]
