import sys
import json
import requests
from config import carregar_config
from session import ChatSession

# ---- Constantes de UI ----

NIVEIS_THINKING = {512: "BAIXO", 1024: "MÉDIO", 2048: "ALTO"}

def exibir_menu():
    print("\nComandos disponíveis:")
    print("  /system  -> Altera as regras em tempo real")
    print("  /prompt  -> Mostra o System Prompt ativo")
    print("  /think   -> Liga/Desliga o pensamento")
    print("  /clear   -> Limpa o histórico de conversas")
    print("  /debug   -> Alterna modo diagnóstico (desligado/normal/verbose)")
    print("  /help    -> Mostra esta ajuda")
    print("  exit     -> Encerra o programa")
    print("(você também pode usar os comandos em português: /sistema, /prompt, /pensar, /limpar, /diagnostico, /ajuda, sair)")

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
    modo_diagnostico = 0  # 0 = off, 1 = normal, 2 = verbose

    print("=== CHAT INICIADO ===")
    exibir_menu()

    while True:
        # Status na linha de input
        status_think = obter_status_think(session)
        diag_status = ""
        if modo_diagnostico == 1:
            diag_status = " [DIAG NORMAL]"
        elif modo_diagnostico == 2:
            diag_status = " [DIAG VERBOSE]"

        try:
            texto = input(f"\n[Pensamento: {status_think}]{diag_status} > ")
        except (EOFError, KeyboardInterrupt):
            print("\n👋 Encerrando...")
            break

        if not texto.strip():
            continue

        # ---- Comandos ----

        cmd = texto.strip().lower()

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
            continue

        # ---- Mensagem normal ----

        session.add_user_message(texto)

        # Modo verbose: mostra resumo do payload
        if modo_diagnostico == 2:
            payload_preview = session.build_payload()
            print("\n[DIAGNÓSTICO] Payload enviado:")
            preview = {k: v for k, v in payload_preview.items() if k != "messages"}
            preview["num_messages"] = len(payload_preview["messages"])
            print(json.dumps(preview, indent=2, ensure_ascii=False))

        # ---- Envio da requisição ----

        print("\n=== RESPOSTA ===")
        if session.thinking_budget > 0:
            print("🧠 [PENSAMENTO]:\n", end="", flush=True)
        else:
            print("⏳ Pensando...", end="", flush=True)

        resp = None
        try:
            payload = session.build_payload()
            resp = session.send_request(payload)
            resp.raise_for_status()
        except requests.exceptions.Timeout:
            print(f"\n❌ Erro: Tempo limite da requisição excedido.")
            session.remove_last_user_message()
            continue
        except requests.exceptions.HTTPError:
            status = resp.status_code if resp is not None else "?"
            texto_erro = resp.text if resp is not None else ""
            print(f"\n❌ Erro HTTP {status}: {texto_erro}")
            if modo_diagnostico >= 1 and resp is not None:
                print(f"[DIAG] Headers: {resp.headers}")
                print(f"[DIAG] Corpo completo: {resp.text}")
            session.remove_last_user_message()
            continue
        except Exception as e:
            print(f"\n❌ Erro de conexão: {e}")
            session.remove_last_user_message()
            continue

        # ---- Processamento do stream ----

        chunk_count = 0
        cabecalho_resposta_impresso = False
        ultimo_timings = None

        def on_raw_line(line_str):
            nonlocal chunk_count
            chunk_count += 1
            if modo_diagnostico == 2 and line_str.strip():
                print(f"\n[DIAG] Chunk {chunk_count}: {line_str[:300]}{'...' if len(line_str) > 300 else ''}")
            if modo_diagnostico == 1 and chunk_count % 5 == 0:
                print(f"\r⏳ Recebendo... {chunk_count} chunks", end="", flush=True)

        def on_thinking_chunk(text):
            nonlocal cabecalho_resposta_impresso
            # Se já havia indicador de progresso, limpa
            if modo_diagnostico == 1:
                print("\r" + " " * 50, end="", flush=True)
            print(text, end="", flush=True)

        def on_content_chunk(text):
            nonlocal cabecalho_resposta_impresso
            if not cabecalho_resposta_impresso:
                if modo_diagnostico == 1:
                    print("\r" + " " * 50, end="", flush=True)
                if session.thinking_budget > 0:
                    print("\n\n🤖 [RESPOSTA FINAL]:\n", end="", flush=True)
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

        resposta_visivel = session.process_stream(resp, callbacks)

        # ---- Pós-stream ----

        if modo_diagnostico >= 1 and ultimo_timings:
            prompt_n = ultimo_timings.get("prompt_n", "?")
            predicted_n = ultimo_timings.get("predicted_n", "?")
            prompt_ms = ultimo_timings.get("prompt_ms", 0)
            predicted_ms = ultimo_timings.get("predicted_ms", 0)
            print(f"\n\n[DIAG] 📊 Tokens: prompt={prompt_n}, resposta={predicted_n}")
            print(f"[DIAG] ⏱️  Tempo: prompt={prompt_ms:.0f}ms, geração={predicted_ms:.0f}ms")

        if not cabecalho_resposta_impresso:
            print("\r⚠️  Sem resposta recebida.                    ")

        print("\n==========================")

        if resposta_visivel:
            session.add_assistant_message(resposta_visivel)
        else:
            print("ℹ️  A resposta veio vazia. Sua mensagem anterior foi mantida no histórico.")

if __name__ == "__main__":
    main()