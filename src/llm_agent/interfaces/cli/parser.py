"""Argument parser construction for the CLI boundary."""
from __future__ import annotations

import argparse

from llm_agent.interfaces.cli.parser_inspect_options import add_inspect_options


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    parser.add_argument("--home", metavar="DIR", help="diretório-base dos dados da aplicação")
    parser.add_argument("--config", metavar="ARQUIVO", help="arquivo de configuração explícito")
    parser.add_argument(
        "--workspace",
        metavar="DIR",
        help="workspace de trabalho; o padrão ou a obrigatoriedade dependem do comando",
    )
    parser.add_argument("--profile", metavar="NOME", help="perfil de modelo configurado")
    parser.add_argument(
        "--observability-mode",
        choices=("normal", "verbose", "debug", "trace"),
        dest="observability_mode",
        metavar="MODE",
        help="nível seguro de observabilidade da execução (padrão: normal)",
    )
    return parser
def build_parser() -> argparse.ArgumentParser:
    """Build the side-effect-free command parser."""
    from llm_agent import __version__
    common = _common_parser()
    parser = argparse.ArgumentParser(
        prog="llm-agent",
        description="Assistente local modular e independente de modelo.",
        parents=[common],
        argument_default=argparse.SUPPRESS,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subcommands = parser.add_subparsers(dest="command")
    engineering = subcommands.add_parser(
        "test", parents=[common], help="executa operações Engineering", argument_default=argparse.SUPPRESS
    )
    engineering_commands = engineering.add_subparsers(dest="test_command", required=True)
    for name in ("list", "history"):
        item = engineering_commands.add_parser(name, parents=[common])
        item.add_argument("--json", action="store_true", dest="json_output")
    compare = engineering_commands.add_parser("compare", parents=[common], help="compara os dois perfis PRACTICAL fixos")
    compare.add_argument("--json", action="store_true", dest="json_output")
    describe = engineering_commands.add_parser("describe", parents=[common])
    describe.add_argument("operation_id")
    describe.add_argument("--json", action="store_true", dest="json_output")
    result = engineering_commands.add_parser("result", parents=[common])
    result.add_argument("run_id")
    result.add_argument("--json", action="store_true", dest="json_output")
    engineering_run = engineering_commands.add_parser("run", parents=[common])
    engineering_run.add_argument("operation_id", nargs="?")
    engineering_run.add_argument("--params-json")
    engineering_run.add_argument("--fault-json", help="plano de falhas JSON autorizado para a execução determinística")
    engineering_run.add_argument("--allow-external", action="store_true")
    engineering_run.add_argument("--allow-network", action="store_true")
    engineering_run.add_argument("--no-input", action="store_true")
    engineering_run.add_argument("--json", action="store_true", dest="json_output")
    commands = subcommands.add_parser("commands", parents=[common], help="lista comandos locais", argument_default=argparse.SUPPRESS)
    commands.add_argument("query", nargs="?", default="", metavar="QUERY")
    commands.add_argument("--json", action="store_true", dest="json_output")
    commands.add_argument("--semantic", action="store_true", help="usa Discovery semântico se configurado")
    commands.add_argument("--plain", action="store_true", help="emite texto sem cores nem controles")
    queue = subcommands.add_parser(
        "queue",
        add_help=False,
        help="cria, confere e controla baterias de testes do agent; executar usa a LLM",
        argument_default=argparse.SUPPRESS,
    )
    queue.add_argument("--help", action="store_true", dest="queue_help", help="mostra as opções da bateria de testes")
    queue.add_argument("queue_args", nargs=argparse.REMAINDER, metavar="ARGUMENTOS_DA_FILA")
    completion = subcommands.add_parser("completion", parents=[common], help="gera completion local", argument_default=argparse.SUPPRESS)
    completion_commands = completion.add_subparsers(dest="completion_command", required=True)
    completion_commands.add_parser("powershell", parents=[common], help="gera completion PowerShell")
    mcp = subcommands.add_parser("mcp", help="serve optional MCP integrations")
    mcp_commands = mcp.add_subparsers(dest="mcp_command", required=True)
    mcp_engineering = mcp_commands.add_parser("engineering", help="serve model-safe Engineering over MCP stdio")
    mcp_engineering.add_argument("--workspace", required=True, metavar="DIR", help="immutable server workspace")
    mcp_engineering.add_argument("--home", metavar="DIR", help="diretório-base dos dados da aplicação")
    chat = subcommands.add_parser(
        "chat", parents=[common], help="abre o chat interativo (comando padrão)", argument_default=argparse.SUPPRESS
    )
    chat.add_argument("--legacy-session", action="store_true", help="compatibilidade temporária com a sessão antiga")
    subcommands.add_parser(
        "local",
        parents=[common],
        help="abre uma sessão de projeto local; a LLM começa desativada",
        argument_default=argparse.SUPPRESS,
        epilog="Na sessão: /llm status, on, off ou test. /llm test consulta o catálogo /models; não gera texto.",
    )
    run = subcommands.add_parser(
        "run",
        parents=[common],
        help="executa um objetivo sem abrir o chat",
        argument_default=argparse.SUPPRESS,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemplos de diretivas W11:\n"
            '  llm-agent run "/read /smart Analise o repositorio"\n'
            '  llm-agent run "/plan /cautious Refatore parser.py"\n'
            '  llm-agent run "/continue"\n'
            "\n/do nao substitui --yes ou grants de autoridade."
        ),
    )
    run.add_argument("objective", nargs="+", metavar="OBJETIVO")
    run.add_argument(
        "--task-authority",
        action="append",
        dest="task_authority_capabilities",
        metavar="CAPABILITY",
        help="autoridade capability-wide da tarefa; repita para cada capability (nao concede grant/persona e nao substitui --yes)",
    )
    run.add_argument("--json", action="store_true", dest="json_output", help="emite um único documento JSON")
    run.add_argument(
        "--yes", action="store_true", dest="assume_yes", help="aprova efeitos que pedirem consentimento nesta execução"
    )
    doctor = subcommands.add_parser(
        "doctor", parents=[common], help="executa o diagnóstico local", argument_default=argparse.SUPPRESS
    )
    doctor.add_argument("--json", action="store_true", dest="json_output", help="emite um único documento JSON")
    doctor.add_argument("--write-report", action="store_true", help="persiste o relatório no estado da aplicação")
    doctor.add_argument("--online", action="store_true", help="executa também a sonda online limitada do provider")
    config = subcommands.add_parser(
        "config", parents=[common], help="gerencia a configuração versionada", argument_default=argparse.SUPPRESS
    )
    config_commands = config.add_subparsers(dest="config_command", required=True)
    config_commands.add_parser("init", parents=[common], help="inicializa a configuração default")
    config_commands.add_parser("path", parents=[common], help="mostra o caminho efetivo")
    config_commands.add_parser("validate", parents=[common], help="valida a configuração efetiva")
    migrate = config_commands.add_parser("migrate", parents=[common], help="copia uma configuração legada")
    migrate.add_argument("--from", required=True, dest="source", metavar="ARQUIVO")
    state = subcommands.add_parser(
        "state", parents=[common], help="gerencia o estado persistente do workspace", argument_default=argparse.SUPPRESS
    )
    state_commands = state.add_subparsers(dest="state_command", required=True)
    state_migrate = state_commands.add_parser("migrate", parents=[common], help="copia um runtime legado")
    state_migrate.add_argument("--from", required=True, dest="source", metavar="DIR")
    tools = subcommands.add_parser(
        "tools",
        parents=[common],
        help="compatibilidade legada de tools; use extensions para administracao canonica",
        argument_default=argparse.SUPPRESS,
    )
    tools_commands = tools.add_subparsers(dest="tools_command", required=True)
    tools_list = tools_commands.add_parser("list", parents=[common], help="lista extensões registradas")
    tools_list.add_argument("--state", metavar="ARQUIVO", help="arquivo do registro de extensões")
    add = tools_commands.add_parser("add", parents=[common], help="registra uma extensão")
    add.add_argument("id", metavar="ID")
    add.add_argument("--manifest", required=True, metavar="ARQUIVO")
    add.add_argument("--disabled", action="store_true", help="registra a extensão desabilitada")
    add.add_argument("--state", metavar="ARQUIVO", help="arquivo do registro de extensões")
    for command, help_text in (
        ("enable", "habilita uma extensão"),
        ("disable", "desabilita uma extensão"),
        ("doctor", "diagnostica extensões registradas"),
    ):
        item = tools_commands.add_parser(command, parents=[common], help=help_text)
        if command != "doctor":
            item.add_argument("id", metavar="ID")
        item.add_argument("--state", metavar="ARQUIVO", help="arquivo do registro de extensões")
    extensions = subcommands.add_parser(
        "extensions",
        parents=[common],
        help="administra o catalogo moderno e a configuracao de extensions",
        argument_default=argparse.SUPPRESS,
    )
    extensions_commands = extensions.add_subparsers(dest="extensions_command", required=True)
    for command, help_text in (
        ("list", "lista o catalogo moderno e o estado do workspace"),
        ("inspect", "inspeciona a configuracao efetiva do workspace"),
    ):
        item = extensions_commands.add_parser(command, parents=[common], help=help_text)
        item.add_argument("--json", action="store_true", dest="json_output", help="emite um unico documento JSON")
        if command == "inspect":
            item.add_argument("id", nargs="?", metavar="ID", help="filtra uma extension")
    register = extensions_commands.add_parser(
        "register", parents=[common], help="registra um manifest no catalogo moderno"
    )
    register.add_argument("manifest", metavar="MANIFEST")
    register.add_argument("--json", action="store_true", dest="json_output", help="emite um unico documento JSON")
    for command, help_text in (
        ("enable", "habilita uma extension neste workspace"),
        ("disable", "desabilita uma extension neste workspace"),
    ):
        item = extensions_commands.add_parser(command, parents=[common], help=help_text)
        item.add_argument("id", metavar="ID")
        item.add_argument("--json", action="store_true", dest="json_output", help="emite um unico documento JSON")
    for command, help_text in (
        ("grant", "concede uma capability persistente no workspace"),
        ("revoke", "revoga uma capability persistente no workspace"),
    ):
        item = extensions_commands.add_parser(command, parents=[common], help=help_text)
        item.add_argument("id", metavar="ID")
        item.add_argument("capability", metavar="CAPABILITY")
        item.add_argument("--json", action="store_true", dest="json_output", help="emite um unico documento JSON")
    task = subcommands.add_parser(
        "task",
        parents=[common],
        help="consulta autoridade persistida de uma tarefa",
        argument_default=argparse.SUPPRESS,
    )
    task_commands = task.add_subparsers(dest="task_command", required=True)
    context = task_commands.add_parser(
        "context",
        parents=[common],
        help="mostra o contexto confiavel da task definition",
    )
    context.add_argument("--task-id", required=True, dest="task_id", metavar="ID")
    context.add_argument("--phase", dest="phase_id", metavar="PHASE_ID")
    context.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="emite um unico documento JSON",
    )
    status = task_commands.add_parser(
        "status",
        parents=[common],
        help="mostra o estado de continuidade da tarefa",
        argument_default=argparse.SUPPRESS,
    )
    status.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="emite um unico documento JSON",
    )
    resume = task_commands.add_parser(
        "resume",
        parents=[common],
        help="retoma explicitamente a tarefa checkpointada",
        argument_default=argparse.SUPPRESS,
    )
    resume.add_argument(
        "--task-authority",
        action="append",
        dest="task_authority_capabilities",
        metavar="CAPABILITY",
        help="autoridade capability-wide da tarefa; repita para cada capability",
    )
    resume.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="emite um unico documento JSON",
    )
    resume.add_argument(
        "--yes",
        action="store_true",
        dest="assume_yes",
        help="aprova efeitos que pedirem consentimento nesta execução",
    )
    inspect = subcommands.add_parser(
        "inspect",
        parents=[common],
        help="inspeciona traces de observabilidade sem iniciar uma tarefa",
        argument_default=argparse.SUPPRESS,
    )
    add_inspect_options(inspect)
    inspect_commands = inspect.add_subparsers(dest="inspect_command")
    inspect_list = inspect_commands.add_parser(
        "list", parents=[common], help="lista runs retidos", argument_default=argparse.SUPPRESS
    )
    add_inspect_options(inspect_list)
    inspect_show = inspect_commands.add_parser(
        "show", parents=[common], help="mostra um snapshot bounded", argument_default=argparse.SUPPRESS
    )
    add_inspect_options(inspect_show)
    inspect_replay = inspect_commands.add_parser(
        "replay", parents=[common], help="reproduz uma trace sem execução", argument_default=argparse.SUPPRESS
    )
    add_inspect_options(inspect_replay)
    inspect_export = inspect_commands.add_parser(
        "export", parents=[common], help="exporta uma trace redigida", argument_default=argparse.SUPPRESS
    )
    add_inspect_options(inspect_export)
    inspect_export.add_argument("--output", metavar="ARQUIVO", help="destino do bundle")
    inspect_export.add_argument("--force", action="store_true", help="autoriza substituir o destino")
    inspect_export.add_argument("--include-bookmarks", action="store_true", help="inclui anotações de bookmarks")
    inspect_export.add_argument("--metadata-only", action="store_true", help="exporta somente IDs, sequência e tempos; omite payloads e notas")
    inspect_bookmark = inspect_commands.add_parser(
        "bookmark", parents=[common], help="gerencia bookmarks da trace", argument_default=argparse.SUPPRESS
    )
    inspect_bookmark.add_argument("bookmark_command", choices=("add", "remove", "list"))
    inspect_bookmark.add_argument("--run-id", dest="inspect_run_id", metavar="RUN_ID")
    inspect_bookmark.add_argument(
        "--json", action="store_true", dest="json_output", help="emite um único documento JSON"
    )
    inspect_bookmark.add_argument("--sequence", type=int, dest="inspect_sequence", metavar="SEQ")
    inspect_bookmark.add_argument("--note", metavar="NOTA")
    return parser
