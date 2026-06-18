import sys
import json
import requests
from rich.console import Console
from rich.panel import Panel
from config import carregar_config
from session import ChatSession
from logger import logger

console = Console()

# ---- Constantes de UI ----
NIVEIS_THINKING = {512: "BAIXO", 1024: "MÉDIO", 2048: "ALTO"}

def obter_status_think(session: ChatSession) -> str:
    if session.thinking_budget > 0:
        nivel = NIVEIS_THINKING.get(session.thinking_budget, "?")
        return f"[green]LIGADO ({nivel}, {session.thinking_budget})[/green]"
    return "[yellow]OFF[/yellow]"

def main() -> None:
    try:
        config = carregar_config()
    except FileNotFoundError as e:
        sys.exit(1)

    session = ChatSession(config["default_system_prompt"], config)

    # Inicializa o orquestrador e carrega skills
    from agent.orchestrator import Orchestrator
    from agent.skills import load_all_skills
    skills = load_all_skills()
    orchestrator = Orchestrator(session, skills, verbose=False)

    # Carrega automaticamente a memória da sessão anterior
    orchestrator.load_memory_from_file("agent_memory.json")
    
    # Injeta o orquestrador nas skills que precisam
    for skill in skills:
        if hasattr(skill, 'orchestrator'):
            skill.orchestrator = orchestrator

    # Contexto para comandos
    from commands import CommandContext, handle_command, exibir_menu
    ctx = CommandContext(session, orchestrator)

    console.rule("[bold cyan]=== CHAT INICIADO ===[/bold cyan]")
    exibir_menu()

    while True:
        status_think = obter_status_think(session)
        diag_status = ""
        if ctx.modo_diagnostico == 1:
            diag_status = " [yellow][DIAG NORMAL][/yellow]"
        elif ctx.modo_diagnostico == 2:
            diag_status = " [yellow][DIAG VERBOSE][/yellow]"

        agente_status = " [green][AGENTE][/green]" if ctx.modo_agente else ""

        try:
            texto = console.input(f"\n[cyan][Pensar: {status_think}][/cyan]{diag_status}{agente_status} > ")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[bold yellow]👋 Encerrando...[/bold yellow]")
            break

        if not texto.strip():
            continue

        # Passa o comando para o gerenciador
        foi_tratado, deve_sair = handle_command(texto, ctx)
        
        if deve_sair:
            break
            
        if foi_tratado:
            continue

        # ---- Mensagem normal (chat) ----
        session.add_user_message(texto)

        if ctx.modo_diagnostico == 2:
            payload_preview = session.build_payload()
            console.print("\n[bold yellow][DIAGNÓSTICO] Payload enviado:[/bold yellow]")
            preview = {k: v for k, v in payload_preview.items() if k != "messages"}
            preview["num_messages"] = len(payload_preview["messages"])
            console.print_json(data=preview)

        # ---- Envio unificado ----
        console.rule("[bold magenta]=== RESPOSTA ===[/bold magenta]")
        spinner_ativo = False
        resposta_interrompida = False

        try:
            payload = session.build_payload()
            resp = session.send_request(payload, stream=True)
            resp.raise_for_status()
        except requests.exceptions.Timeout:
            console.print("\r[bold red]❌ Erro: Tempo limite da requisição excedido.[/bold red]                    ")
            logger.error("Tempo limite da requisição excedido.")
            session.remove_last_user_message()
            continue
        except requests.exceptions.HTTPError:
            status = resp.status_code if 'resp' in locals() and resp is not None else "?"
            texto_erro = resp.text if 'resp' in locals() and resp is not None else ""
            console.print(f"\r[bold red]❌ Erro HTTP {status}: {texto_erro}[/bold red]                    ")
            logger.error(f"Erro HTTP {status}: {texto_erro}")
            session.remove_last_user_message()
            continue
        except Exception as e:
            console.print(f"\r[bold red]❌ Erro de conexão: {e}[/bold red]                    ")
            logger.error(f"Erro de conexão: {e}", exc_info=True)
            session.remove_last_user_message()
            continue

        # ---- Callbacks para process_stream (unificado) ----
        chunk_count = 0
        cabecalho_resposta_impresso = False
        ultimo_timings = None
        
        # Para usar o spinner do rich corretamente em streaming, podemos usar rich.status
        # Mas para simplificar o print streaming chunk-by-chunk, o texto bruto é impresso direto.
        # Mantivemos a estrutura básica, mas com cores ricas no cabeçalho.
        
        def on_raw_line(line_str: str) -> None:
            nonlocal chunk_count
            chunk_count += 1
            if ctx.modo_diagnostico == 2 and line_str.strip():
                console.print(f"\n[dim yellow][DIAG] Chunk {chunk_count}: {line_str[:300]}{'...' if len(line_str) > 300 else ''}[/dim yellow]")
            if ctx.modo_diagnostico == 1 and chunk_count % 5 == 0:
                print(f"\r⏳ Recebendo... {chunk_count} chunks", end="", flush=True)

        def on_thinking_chunk(text: str) -> None:
            nonlocal spinner_ativo, cabecalho_resposta_impresso
            if not spinner_ativo:
                if ctx.modo_diagnostico == 1:
                    print("\r" + " " * 50, end="", flush=True)
                console.print("[bold cyan]🧠 [PENSAMENTO]:[/bold cyan]")
                spinner_ativo = True
            print(text, end="", flush=True)

        def on_content_chunk(text: str) -> None:
            nonlocal cabecalho_resposta_impresso, spinner_ativo
            if not cabecalho_resposta_impresso:
                if ctx.modo_diagnostico == 1:
                    print("\r" + " " * 50, end="", flush=True)
                if spinner_ativo and session.thinking_budget > 0:
                    console.print("\n\n[bold green]🤖 [RESPOSTA FINAL]:[/bold green]")
                else:
                    print("\r", end="")
                    console.print("[bold green]🤖 [RESPOSTA]:[/bold green]")
                cabecalho_resposta_impresso = True
            # LLMs stream chars naturally, print is safest
            print(text, end="", flush=True)

        def on_error(msg: str) -> None:
            console.print(f"\n\n[bold red]❌ Erro do servidor: {msg}[/bold red]")
            logger.error(f"Erro reportado pelo servidor no stream: {msg}")

        def on_done(timings: dict) -> None:
            nonlocal ultimo_timings
            ultimo_timings = timings

        callbacks = {
            "on_raw_line": on_raw_line,
            "on_thinking_chunk": on_thinking_chunk,
            "on_content_chunk": on_content_chunk,
            "on_error": on_error,
            "on_done": on_done
        }

        try:
            resposta_visivel = session.process_stream(resp, callbacks)
        except KeyboardInterrupt:
            console.print("\r[bold red]⚠️  Interrompido pelo usuário.[/bold red]                    ")
            logger.warning("Geração de resposta interrompida pelo usuário.")
            resposta_visivel = ""
            resposta_interrompida = True

        if ctx.modo_diagnostico >= 1 and ultimo_timings:
            prompt_n = ultimo_timings.get("prompt_n", "?")
            predicted_n = ultimo_timings.get("predicted_n", "?")
            prompt_ms = ultimo_timings.get("prompt_ms", 0)
            predicted_ms = ultimo_timings.get("predicted_ms", 0)
            console.print(f"\n\n[bold yellow][DIAG] 📊 Tokens: prompt={prompt_n}, resposta={predicted_n}[/bold yellow]")
            console.print(f"[bold yellow][DIAG] ⏱️  Tempo: prompt={prompt_ms:.0f}ms, geração={predicted_ms:.0f}ms[/bold yellow]")

        if not cabecalho_resposta_impresso and not resposta_interrompida:
            console.print("\r[bold red]⚠️  Sem resposta recebida.[/bold red]                    ")

        print() # Quebra de linha final

        if resposta_visivel and not resposta_interrompida:
            session.add_assistant_message(resposta_visivel)
        elif resposta_interrompida:
            console.print("[bold yellow]ℹ️  Resposta interrompida. Sua mensagem anterior foi mantida no histórico.[/bold yellow]")
        else:
            console.print("[bold yellow]ℹ️  A resposta veio vazia. Sua mensagem anterior foi mantida no histórico.[/bold yellow]")

if __name__ == "__main__":
    main()