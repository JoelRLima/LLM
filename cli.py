import sys
import json
import itertools
import requests
from config import carregar_config
from session import ChatSession

# ---- Constantes de UI ----

NIVEIS_THINKING = {512: "BAIXO", 1024: "MÉDIO", 2048: "ALTO"}
SPINNER = itertools.cycle(["|", "/", "-", "\\"])

def exibir_menu():
    print("\nComandos disponíveis:")
    print("  /system  -> Altera as regras em tempo real")
    print("  /prompt  -> Mostra o System Prompt ativo")
    print("  /think   -> Liga/Desliga o pensamento")
    print("  /clear   -> Limpa o histórico de conversas")
    print("  /save    -> Salva o histórico em um arquivo")
    print("  /load    -> Carrega o histórico de um arquivo")
    print("  /agent   -> Ativa/desativa o modo agente (toggle)")
    print("  /agent <objetivo> -> Executa um objetivo avulso no modo agente")
    print("  /debug   -> Alterna modo diagnóstico (desligado/normal/verbose)")
    print("  /help    -> Mostra esta ajuda")
    print("  exit     -> Encerra o programa")
    print("(você também pode usar os comandos em português: /sistema, /prompt, /pensar, /limpar, /salvar, /carregar, /agente, /diagnostico, /ajuda, sair)")

def obter_status_think(session):
    if session.thinking_budget > 0:
        nivel = NIVEIS_THINKING.get(session.thinking_budget, "?")
        return f"LIGADO ({nivel}, teto {session.thinking_budget} tokens)"
    return "DESLIGADO"

def main():
    try:
        config = carregar_config()
    except FileNotFoundError as e:
        print(f"❌ Erro: {e}")
        sys.exit(1)

    session = ChatSession(config["default_system_prompt"], config)
    modo_diagnostico = 0
    modo_agente = True

    # Inicializa o orquestrador uma única vez com as skills
    from agent.orchestrator import Orchestrator
    from agent.skills import load_all_skills
    skills = load_all_skills()
    orchestrator = Orchestrator(session, skills, verbose=(modo_diagnostico >= 1))

    # Injeta o orquestrador nas skills que precisam
    for skill in skills:
        if hasattr(skill, 'orchestrator'):
            skill.orchestrator = orchestrator

    print("=== CHAT INICIADO ===")
    exibir_menu()

    while True:
        status_think = obter_status_think(session)
        diag_status = ""
        if modo_diagnostico == 1:
            diag_status = " [DIAG NORMAL]"
        elif modo_diagnostico == 2:
            diag_status = " [DIAG VERBOSE]"

        agente_status = " [AGENTE]" if modo_agente else ""

        try:
            texto = input(f"\n[Pensamento: {status_think}]{diag_status}{agente_status} > ")
        except (EOFError, KeyboardInterrupt):
            print("\n👋 Encerrando...")
            break

        if not texto.strip():
            continue

        cmd = texto.strip().lower()

        # ---- Comandos ----

        if cmd in ["sair", "exit"]:
            break

        if cmd in ["/system", "/sistema"]:
            novo = input("Digite o novo System Prompt: ")
            if novo.strip():
                session.set_system_prompt(novo)
                print("✅ System Prompt atualizado!")
            continue

        if cmd == "/prompt":
            print(f"📌 Prompt ativo:\n{session.get_effective_system_prompt()}")
            continue

        if cmd in ["/think", "/pensar"]:
            if session.thinking_budget == 0:
                escolha = input("Tokens (B=baixo, M=médio, A=alto, ou número): ").strip().upper()
                mapeamento = {"B": 512, "M": 1024, "A": 2048}
                if escolha in mapeamento:
                    session.thinking_budget = mapeamento[escolha]
                else:
                    try:
                        session.thinking_budget = int(escolha)
                    except ValueError:
                        session.thinking_budget = 1024
                print(f"🧠 Thinking ON (teto: {session.thinking_budget} tokens)")
            else:
                session.thinking_budget = 0
                print("⚡ Thinking OFF")
            continue

        if cmd in ["/clear", "/limpar"]:
            session.clear_history()
            print("🧹 Histórico limpo!")
            continue

        if cmd in ["/save", "/salvar"]:
            caminho = input("Caminho do arquivo (ou Enter para 'chat_history.json'): ").strip()
            if not caminho:
                caminho = "chat_history.json"
            sucesso, erro = session.save_to_file(caminho)
            if sucesso:
                print(f"💾 Histórico salvo em '{caminho}'.")
            else:
                print(f"❌ Erro ao salvar: {erro}")
            continue

        if cmd in ["/load", "/carregar"]:
            caminho = input("Caminho do arquivo (ou Enter para 'chat_history.json'): ").strip()
            if not caminho:
                caminho = "chat_history.json"
            sucesso, erro = session.load_from_file(caminho)
            if sucesso:
                print(f"📂 Histórico carregado de '{caminho}'.")
            else:
                print(f"❌ Erro ao carregar: {erro}")
            continue

        if cmd in ["/help", "/ajuda"]:
            exibir_menu()
            continue

        if cmd in ["/debug", "/diagnostico"]:
            if modo_diagnostico == 0:
                modo_diagnostico = 1
                print("🔧 Diagnóstico LIGADO (modo normal: progresso + resumo). Use /debug novamente para modo verbose.")
            elif modo_diagnostico == 1:
                modo_diagnostico = 2
                print("🔧 Diagnóstico VERBOSE (mostra cada chunk). Use /debug mais uma vez para desligar.")
            else:
                modo_diagnostico = 0
                print("🔧 Diagnóstico DESLIGADO.")
            # 👇 LINHA NOVA: mantém o verbose do agente sincronizado
            orchestrator.verbose = (modo_diagnostico >= 1)
            continue

        # ---- Comando /agent (toggle e execução avulsa) ----
        if cmd.startswith("/agent") or cmd.startswith("/agente"):
            # Extrai possível objetivo
            partes = texto.strip().split(maxsplit=1)
            if len(partes) == 1:
                # Sem argumentos: alterna o modo
                modo_agente = not modo_agente
                estado = "LIGADO" if modo_agente else "DESLIGADO"
                print(f"🤖 Modo agente {estado}.")
            else:
                # Com argumentos: executa objetivo avulso (não altera o modo atual)
                objetivo = partes[1]
                print(f"🚀 Executando objetivo avulso: {objetivo}")
                try:
                    resposta = orchestrator.run(objetivo)
                    print(f"\n🤖 Agente: {resposta}")
                    session.add_assistant_message(resposta)
                except KeyboardInterrupt:
                    print("\n⚠️ Agente interrompido.")
            continue
        # ---- Memória ----
        if cmd.startswith("/remember"):
            partes = texto.strip().split(maxsplit=2)
            if len(partes) >= 3:
                chave = partes[1]
                valor = partes[2]
                orchestrator.remember(chave, valor)
                print(f"🧠 Lembrei: {chave} = {valor}")
            else:
                print("Uso: /remember chave valor")
            continue

        if cmd in ["/memory", "/memoria"]:
            print("🧠 Memória da sessão:")
            for section, content in orchestrator.memory.items():
                if content:
                    print(f"\n[{section}]")
                    if isinstance(content, dict):
                        for k, v in content.items():
                            print(f"  {k}: {v}")
                    elif isinstance(content, list):
                        for item in content:
                            print(f"  - {item}")
            continue

        if cmd in ["/forget", "/esquecer"]:
            chave = input("Chave a esquecer: ").strip()
            orchestrator.forget(chave)
            print(f"🧠 Chave '{chave}' removida (se existia).")
            continue

        if cmd in ["/clearmemory", "/limpamemoria"]:
            orchestrator.clear_memory()
            print("🧠 Memória da sessão limpa.")
            continue

        if cmd in ["/save_memory", "/salvarmemoria"]:
            caminho = input("Caminho (Enter para 'agent_memory.json'): ").strip()
            if not caminho:
                caminho = "agent_memory.json"
            msg = orchestrator.save_memory_to_file(caminho)
            print(f"💾 {msg}")
            continue

        if cmd in ["/load_memory", "/carregarmemoria"]:
            caminho = input("Caminho (Enter para 'agent_memory.json'): ").strip()
            if not caminho:
                caminho = "agent_memory.json"
            msg = orchestrator.load_memory_from_file(caminho)
            print(f"📂 {msg}")
            continue

        # ---- Se modo agente ativo e não é comando, trata como objetivo ----
        if modo_agente and not texto.startswith("/"):
            try:
                resposta = orchestrator.run(texto)
                print(f"\n🤖 Agente: {resposta}")
                session.add_assistant_message(resposta)
            except KeyboardInterrupt:
                print("\n⚠️ Agente interrompido.")
            continue

        # ---- Mensagem normal (chat) ----

        session.add_user_message(texto)

        if modo_diagnostico == 2:
            payload_preview = session.build_payload()
            print("\n[DIAGNÓSTICO] Payload enviado:")
            preview = {k: v for k, v in payload_preview.items() if k != "messages"}
            preview["num_messages"] = len(payload_preview["messages"])
            print(json.dumps(preview, indent=2, ensure_ascii=False))

        # ---- Envio unificado ----
        print("\n=== RESPOSTA ===")
        spinner_ativo = False
        resposta_interrompida = False

        try:
            payload = session.build_payload()
            resp = session.send_request(payload, stream=True)
            resp.raise_for_status()
        except requests.exceptions.Timeout:
            print(f"\r❌ Erro: Tempo limite da requisição excedido.                    ")
            session.remove_last_user_message()
            continue
        except requests.exceptions.HTTPError:
            status = resp.status_code if 'resp' in locals() and resp is not None else "?"
            texto_erro = resp.text if 'resp' in locals() and resp is not None else ""
            print(f"\r❌ Erro HTTP {status}: {texto_erro}                    ")
            if modo_diagnostico >= 1 and 'resp' in locals() and resp is not None:
                print(f"[DIAG] Headers: {resp.headers}")
                print(f"[DIAG] Corpo completo: {resp.text}")
            session.remove_last_user_message()
            continue
        except Exception as e:
            print(f"\r❌ Erro de conexão: {e}                    ")
            session.remove_last_user_message()
            continue

        # ---- Callbacks para process_stream (unificado) ----
        chunk_count = 0
        cabecalho_resposta_impresso = False
        ultimo_timings = None
        spinner_state = {"last_spin": 0}

        def on_raw_line(line_str):
            nonlocal chunk_count, spinner_ativo
            chunk_count += 1
            if not cabecalho_resposta_impresso and session.thinking_budget == 0:
                if not spinner_ativo:
                    spinner_ativo = True
                if chunk_count - spinner_state["last_spin"] >= 2:
                    print(f"\r⏳ Gerando resposta... {next(SPINNER)}", end="", flush=True)
                    spinner_state["last_spin"] = chunk_count

            if modo_diagnostico == 2 and line_str.strip():
                print(f"\n[DIAG] Chunk {chunk_count}: {line_str[:300]}{'...' if len(line_str) > 300 else ''}")
            if modo_diagnostico == 1 and chunk_count % 5 == 0:
                print(f"\r⏳ Recebendo... {chunk_count} chunks", end="", flush=True)

        def on_thinking_chunk(text):
            nonlocal spinner_ativo, cabecalho_resposta_impresso
            if not spinner_ativo:
                if modo_diagnostico == 1:
                    print("\r" + " " * 50, end="", flush=True)
                print("🧠 [PENSAMENTO]:\n", end="", flush=True)
                spinner_ativo = True
            print(text, end="", flush=True)

        def on_content_chunk(text):
            nonlocal cabecalho_resposta_impresso, spinner_ativo
            if not cabecalho_resposta_impresso:
                if modo_diagnostico == 1:
                    print("\r" + " " * 50, end="", flush=True)
                if spinner_ativo and session.thinking_budget > 0:
                    print("\n🤖 [RESPOSTA FINAL]:\n", end="", flush=True)
                else:
                    print(f"\r🤖 [RESPOSTA]:{'':20}\n", end="", flush=True)
                cabecalho_resposta_impresso = True
            print(text, end="", flush=True)

        def on_error(msg):
            print(f"\n\n❌ Erro do servidor: {msg}")

        def on_done(timings):
            nonlocal ultimo_timings
            ultimo_timings = timings

        callbacks = {
            "on_raw_line": on_raw_line,
            "on_thinking_chunk": on_thinking_chunk,
            "on_content_chunk": on_content_chunk,
            "on_error": on_error,
            "on_done": on_done
        }

        if session.thinking_budget == 0:
            print("⏳ Gerando resposta...", end="", flush=True)

        try:
            resposta_visivel = session.process_stream(resp, callbacks)
        except KeyboardInterrupt:
            print("\r⚠️  Interrompido pelo usuário.                    ")
            resposta_visivel = ""
            resposta_interrompida = True

        if modo_diagnostico >= 1 and ultimo_timings:
            prompt_n = ultimo_timings.get("prompt_n", "?")
            predicted_n = ultimo_timings.get("predicted_n", "?")
            prompt_ms = ultimo_timings.get("prompt_ms", 0)
            predicted_ms = ultimo_timings.get("predicted_ms", 0)
            print(f"\n\n[DIAG] 📊 Tokens: prompt={prompt_n}, resposta={predicted_n}")
            print(f"[DIAG] ⏱️  Tempo: prompt={prompt_ms:.0f}ms, geração={predicted_ms:.0f}ms")

        if not cabecalho_resposta_impresso and not resposta_interrompida:
            print("\r⚠️  Sem resposta recebida.                    ")

        print("\n==========================")

        if resposta_visivel and not resposta_interrompida:
            session.add_assistant_message(resposta_visivel)
        elif resposta_interrompida:
            print("ℹ️  Resposta interrompida. Sua mensagem anterior foi mantida no histórico.")
        else:
            print("ℹ️  A resposta veio vazia. Sua mensagem anterior foi mantida no histórico.")

if __name__ == "__main__":
    main()