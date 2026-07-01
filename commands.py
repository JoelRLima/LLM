from typing import Tuple
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from session import ChatSession
from agent.orchestrator import Orchestrator
from logger import logger, set_debug_level

console = Console()

class CommandContext:
    def __init__(self, session: ChatSession, orchestrator: Orchestrator) -> None:
        self.session = session
        self.orchestrator = orchestrator
        self.modo_diagnostico: int = 0
        self.modo_agente: bool = True

def exibir_menu() -> None:
    table = Table(title="Comandos Disponíveis", show_header=True, header_style="bold magenta")
    table.add_column("Comando", style="cyan", width=20)
    table.add_column("Descrição")

    table.add_row("/system, /sistema", "Altera as regras em tempo real")
    table.add_row("/prompt", "Mostra o System Prompt ativo")
    table.add_row("/think, /pensar", "Liga/Desliga o pensamento")
    table.add_row("/clear, /limpar", "Limpa o histórico de conversas")
    table.add_row("/save, /salvar", "Salva o histórico em um arquivo")
    table.add_row("/load, /carregar", "Carrega o histórico de um arquivo")
    table.add_row("/agent, /agente", "Ativa/desativa o modo agente (toggle)")
    table.add_row("/agent <objetivo>", "Executa um objetivo avulso no modo agente")
    table.add_row("/debug", "Alterna modo diagnóstico")
    table.add_row("/memory, /memoria", "Mostra o estado da memória do agente")
    table.add_row("/events", "Mostra os eventos da última execução")
    table.add_row("/doctor, /diagnostico", "Executa o diagnóstico de saúde do agente.")
    table.add_row("/retry, /retomar", "Retoma a tarefa interrompida (checkpoint)")
    table.add_row("/ls, /list", "Lista os arquivos do projeto (atalho)")
    table.add_row("/read <arquivo>", "Lê o arquivo diretamente (atalho)")
    table.add_row("/find <texto>", "Busca por texto nos arquivos (atalho)")
    table.add_row("/search <consulta>", "Pesquisa na web (atalho)")
    table.add_row("/help, /ajuda", "Mostra esta ajuda")
    table.add_row("exit, sair", "Encerra o programa")

    console.print(table)

def handle_command(texto: str, ctx: CommandContext) -> Tuple[bool, bool]:
    """
    Processa a entrada do usuário.
    Retorna (foi_tratado_aqui, deve_sair).
    """
    cmd = texto.strip().lower()

    if cmd in ["sair", "exit"]:
        return True, True

    if cmd in ["/help", "/ajuda"]:
        exibir_menu()
        return True, False

    if cmd in ["/system", "/sistema"]:
        novo = console.input("[bold cyan]Digite o novo System Prompt:[/bold cyan] ")
        if novo.strip():
            ctx.session.set_system_prompt(novo)
            console.print("[bold green]✅ System Prompt atualizado![/bold green]")
        return True, False

    if cmd == "/prompt":
        console.print(Panel(ctx.session.get_effective_system_prompt(), title="[bold blue]📌 Prompt ativo[/bold blue]"))
        return True, False

    if cmd in ["/think", "/pensar"]:
        if ctx.session.thinking_budget == 0:
            escolha = console.input("[bold cyan]Tokens (B=baixo, M=médio, A=alto, ou número):[/bold cyan] ").strip().upper()
            mapeamento = {"B": 512, "M": 1024, "A": 2048}
            if escolha in mapeamento:
                ctx.session.thinking_budget = mapeamento[escolha]
            else:
                try:
                    ctx.session.thinking_budget = int(escolha)
                except ValueError:
                    ctx.session.thinking_budget = 1024
            console.print(f"[bold green]🧠 Thinking ON (teto: {ctx.session.thinking_budget} tokens)[/bold green]")
        else:
            ctx.session.thinking_budget = 0
            console.print("[bold yellow]⚡ Thinking OFF[/bold yellow]")
        return True, False

    if cmd in ["/clear", "/limpar"]:
        ctx.session.clear_history()
        console.print("[bold green]🧹 Histórico limpo![/bold green]")
        return True, False

    if cmd in ["/save", "/salvar"]:
        caminho = console.input("[bold cyan]Caminho do arquivo (ou Enter para 'chat_history.json'):[/bold cyan] ").strip()
        if not caminho:
            caminho = "chat_history.json"
        sucesso, erro = ctx.session.save_to_file(caminho)
        if sucesso:
            console.print(f"[bold green]💾 Histórico salvo em '{caminho}'.[/bold green]")
        else:
            console.print(f"[bold red]❌ Erro ao salvar: {erro}[/bold red]")
        return True, False

    if cmd in ["/load", "/carregar"]:
        caminho = console.input("[bold cyan]Caminho do arquivo (ou Enter para 'chat_history.json'):[/bold cyan] ").strip()
        if not caminho:
            caminho = "chat_history.json"
        sucesso, erro = ctx.session.load_from_file(caminho)
        if sucesso:
            console.print(f"[bold green]📂 Histórico carregado de '{caminho}'.[/bold green]")
        else:
            console.print(f"[bold red]❌ Erro ao carregar: {erro}[/bold red]")
        return True, False

    if cmd in ["/debug", "/diagnostico"]:
        if ctx.modo_diagnostico == 0:
            ctx.modo_diagnostico = 1
            set_debug_level(1)
            console.print("[bold yellow]🔧 Diagnóstico LIGADO (modo normal: progresso + resumo). Use /debug novamente para modo verbose.[/bold yellow]")
        elif ctx.modo_diagnostico == 1:
            ctx.modo_diagnostico = 2
            console.print("[bold yellow]🔧 Diagnóstico VERBOSE (mostra cada chunk). Use /debug mais uma vez para desligar.[/bold yellow]")
        else:
            ctx.modo_diagnostico = 0
            set_debug_level(0)
            console.print("[bold green]🔧 Diagnóstico DESLIGADO.[/bold green]")
        ctx.orchestrator.verbose = (ctx.modo_diagnostico >= 1)
        ctx.orchestrator.context_manager.verbose = ctx.orchestrator.verbose
        return True, False

    if cmd.startswith("/agent") or cmd.startswith("/agente"):
        partes = texto.strip().split(maxsplit=1)
        if len(partes) == 1:
            ctx.modo_agente = not ctx.modo_agente
            estado = "[bold green]LIGADO[/bold green]" if ctx.modo_agente else "[bold red]DESLIGADO[/bold red]"
            console.print(f"🤖 Modo agente {estado}.")
        else:
            objetivo = partes[1]
            console.print(f"[bold magenta]🚀 Executando objetivo avulso:[/bold magenta] {objetivo}")
            try:
                resposta = ctx.orchestrator.run(objetivo)
                console.print(Panel(resposta, title="[bold blue]🤖 Agente[/bold blue]"))
                ctx.session.add_assistant_message(resposta)
            except KeyboardInterrupt:
                console.print("\n[bold red]⚠️ Agente interrompido.[/bold red]")
        return True, False

    if cmd.startswith("/remember"):
        partes = texto.strip().split(maxsplit=2)
        if len(partes) >= 3:
            chave = partes[1]
            valor = partes[2]
            ctx.orchestrator.remember(chave, valor)
            console.print(f"[bold green]🧠 Lembrei:[/bold green] {chave} = {valor}")
        else:
            console.print("[bold red]Uso: /remember chave valor[/bold red]")
        return True, False

    if cmd in ["/memory", "/memoria"]:
        t = Table(title="Memória da Sessão")
        t.add_column("Seção", style="cyan")
        t.add_column("Conteúdo")
        for section, content in ctx.orchestrator.agent_state.memory.state.items():
            if content:
                t.add_row(section, str(content))
        console.print(t)
        return True, False

    if cmd in ["/events"]:
        if not ctx.orchestrator.agent_state.events:
            console.print("[yellow]Nenhum evento registrado.[/yellow]")
        else:
            for ev in ctx.orchestrator.agent_state.events:
                console.print(f"[dim]Passo {ev['step']}:[/dim] {ev['type']} {ev['data']}")
        return True, False

    if cmd in ["/forget", "/esquecer"]:
        chave = console.input("[bold cyan]Chave a esquecer:[/bold cyan] ").strip()
        ctx.orchestrator.forget(chave)
        console.print(f"[bold green]🧠 Chave '{chave}' removida (se existia).[/bold green]")
        return True, False

    if cmd in ["/clearmemory", "/limpamemoria"]:
        ctx.orchestrator.clear_memory()
        console.print("[bold green]🧠 Memória da sessão limpa.[/bold green]")
        return True, False

    if cmd in ["/save_memory", "/salvarmemoria"]:
        caminho = console.input("[bold cyan]Caminho (Enter para 'agent_memory.json'):[/bold cyan] ").strip()
        if not caminho:
            caminho = "agent_memory.json"
        msg = ctx.orchestrator.save_memory_to_file(caminho)
        console.print(f"[bold green]💾 {msg}[/bold green]")
        return True, False

    if cmd in ["/load_memory", "/carregarmemoria"]:
        caminho = console.input("[bold cyan]Caminho (Enter para 'agent_memory.json'):[/bold cyan] ").strip()
        if not caminho:
            caminho = "agent_memory.json"
        msg = ctx.orchestrator.load_memory_from_file(caminho)
        console.print(f"[bold green]📂 {msg}[/bold green]")
        return True, False
    
    if cmd in ("/doctor", "/diagnostico"):
        from agent.health_check import run_health_check
        run_health_check()
        return True, False

    if cmd in ("/ls", "/list"):
        skill = ctx.orchestrator.skills.get("directory_lister")
        if skill:
            result = skill.execute({"path": "."})
            if result.get("ok"):
                data = result.get("data", [])
                for item in data:
                    tipo = item.get("type", "")
                    nome = item.get("name", "")
                    console.print(f"  {'📁' if tipo == 'dir' else '📄'} {nome}")
                console.print(f"\n[dim]{len(data)} itens[/dim]")
            else:
                console.print(f"[red]Erro: {result.get('error', 'desconhecido')}[/red]")
        else:
            console.print("[red]Skill 'directory_lister' não disponível.[/red]")
        return True, False

    if cmd.startswith("/read"):
        partes = texto.strip().split(maxsplit=1)
        file_path = partes[1].strip() if len(partes) > 1 else ""
        if not file_path:
            console.print("[red]Uso: /read <arquivo>[/red]")
            return True, False
        skill = ctx.orchestrator.skills.get("file_reader")
        if skill:
            result = skill.execute({"file_path": file_path})
            if result.get("ok"):
                console.print(result.get("data", ""))
            else:
                console.print(f"[red]Erro: {result.get('error', 'desconhecido')}[/red]")
        else:
            console.print("[red]Skill 'file_reader' não disponível.[/red]")
        return True, False

    if cmd.startswith("/find"):
        partes = texto.strip().split(maxsplit=1)
        pattern = partes[1].strip() if len(partes) > 1 else ""
        if not pattern:
            console.print("[red]Uso: /find <texto>[/red]")
            return True, False
        skill = ctx.orchestrator.skills.get("grep")
        if skill:
            result = skill.execute({"pattern": pattern, "path": "."})
            if result.get("ok"):
                data = result.get("data", "")
                console.print(data if data else "[yellow]Nenhuma ocorrência encontrada.[/yellow]")
            else:
                console.print(f"[red]Erro: {result.get('error', 'desconhecido')}[/red]")
        else:
            console.print("[red]Skill 'grep' não disponível.[/red]")
        return True, False

    if cmd.startswith("/search"):
        partes = texto.strip().split(maxsplit=1)
        query = partes[1].strip() if len(partes) > 1 else ""
        if not query:
            console.print("[red]Uso: /search <consulta>[/red]")
            return True, False
        skill = ctx.orchestrator.skills.get("web_search")
        if skill:
            result = skill.execute({"query": query})
            if result.get("ok"):
                console.print(result.get("data", ""))
            else:
                console.print(f"[red]Erro: {result.get('error', 'desconhecido')}[/red]")
        else:
            console.print("[red]Skill 'web_search' não disponível.[/red]")
        return True, False
    
    if cmd in ("/retry", "/retomar"):
        console.print("[bold yellow]🔄 Verificando checkpoint...[/bold yellow]")
        resposta = ctx.orchestrator.run(None, stream_callback=None)  # None para retomar
        console.print(Panel(resposta, title="[bold blue]🤖 Agente[/bold blue]"))
        ctx.session.add_assistant_message(resposta)
        return True, False

    if ctx.modo_agente and not texto.startswith("/"):
        def on_agent_chunk(text: str) -> None:
            print(text, end="", flush=True)

        console.print("[bold blue]🤖 Agente:[/bold blue]")
        # O cancelamento (Ctrl+C) agora é tratado de forma cooperativa dentro
        # de Orchestrator.run(), que salva o checkpoint e retorna uma
        # mensagem informando o cancelamento, em vez de propagar a exceção.
        resposta = ctx.orchestrator.run(texto, stream_callback=on_agent_chunk)
        print()  # quebra de linha após o fim do streaming

        if ctx.modo_diagnostico >= 1:
            console.print(Panel(resposta, title="Resposta do Agente", border_style="blue"))
        ctx.session.add_assistant_message(resposta)
        return True, False