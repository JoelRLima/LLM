import requests
import json
import os
import sys

CONFIG_FILE = "config.json"
if not os.path.exists(CONFIG_FILE):
    print(f"❌ Erro: O arquivo '{CONFIG_FILE}' não foi encontrado!")
    sys.exit(1)

with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    config = json.load(f)

# System prompt padrão: raciocinar em inglês, responder em português
default_prompt = (
    "You are a helpful assistant. "
    "Always think and reason in English. "
    "Your final response must be in Portuguese (Brazil), natural and fluent. "
    "Do not mention the language switch."
)

messages = [
    {
        "role": "system",
        "content": config.get("default_system_prompt", default_prompt)
    }
]
orcamento_pensamento = 0
# 0 = desligado, 1 = normal (progresso + resumo), 2 = verbose (mostra chunks)
modo_diagnostico = 0

def obter_system_prompt_efetivo():
    """Retorna o system prompt com a instrução de pensamento (se ativa)."""
    if orcamento_pensamento > 0:
        return (
            messages[0]["content"]
            + f"\n\n[THINKING]: You may spend up to {orcamento_pensamento} tokens thinking. "
            "This is a maximum limit, not a target. Stop as soon as you have a satisfactory answer. "
            "Be concise."
        )
    else:
        return messages[0]["content"]

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

print("=== CHAT INICIADO ===")
exibir_menu()

while True:
    # Exibe status do pensamento com nível e teto
    if orcamento_pensamento > 0:
        niveis = {512: "BAIXO", 1024: "MÉDIO", 2048: "ALTO"}
        nivel = niveis.get(orcamento_pensamento, "?")
        status_think = f"LIGADO ({nivel}, teto {orcamento_pensamento} tokens)"
    else:
        status_think = "DESLIGADO"

    # Status do modo diagnóstico
    diag_status = ""
    if modo_diagnostico == 1:
        diag_status = " [DIAG NORMAL]"
    elif modo_diagnostico == 2:
        diag_status = " [DIAG VERBOSE]"

    texto = input(f"\n[Pensamento: {status_think}]{diag_status} > ")

    if not texto.strip():
        continue

    # Saída (inglês e português)
    if texto.lower() in ["sair", "exit"]:
        break

    # /system (aceita /system e /sistema)
    if texto.strip().lower() in ["/system", "/sistema"]:
        novo_prompt = input("Digite o novo System Prompt: ")
        if novo_prompt.strip():
            messages[0]["content"] = novo_prompt
            print("✅ System Prompt atualizado!")
        continue

    # /prompt (exibe o prompt efetivo)
    if texto.strip().lower() == "/prompt":
        print(f"📌 Prompt ativo:\n{obter_system_prompt_efetivo()}")
        continue

    # /think (aceita /think e /pensar)
    if texto.strip().lower() in ["/think", "/pensar"]:
        if orcamento_pensamento == 0:
            escolha = input("Tokens (B=baixo, M=médio, A=alto, ou número): ").strip().upper()
            mapeamento = {"B": 512, "M": 1024, "A": 2048}
            if escolha in mapeamento:
                orcamento_pensamento = mapeamento[escolha]
            else:
                try:
                    orcamento_pensamento = int(escolha)
                except ValueError:
                    orcamento_pensamento = 1024  # fallback
            print(f"🧠 Thinking ON (teto: {orcamento_pensamento} tokens)")
        else:
            orcamento_pensamento = 0
            print("⚡ Thinking OFF")
        continue

    # /clear (aceita /clear e /limpar)
    if texto.strip().lower() in ["/clear", "/limpar"]:
        messages = [{"role": "system", "content": messages[0]["content"]}]
        print("🧹 Histórico limpo!")
        continue

    # /help (aceita /help e /ajuda)
    if texto.strip().lower() in ["/help", "/ajuda"]:
        exibir_menu()
        continue

    # /debug (aceita /debug e /diagnostico) - agora com 3 níveis
    if texto.strip().lower() in ["/debug", "/diagnostico"]:
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

    # Adiciona entrada do usuário
    messages.append({"role": "user", "content": texto})

    # Monta o payload usando a função centralizada
    system_content = obter_system_prompt_efetivo()

    payload = {
        "model": config.get("model", "default"),
        "messages": [{"role": "system", "content": system_content}] + messages[1:],
        "temperature": config.get("temperature", 0.6),
        "max_tokens": config.get("max_tokens", 4096),
        "stream": True,
        "chat_template_kwargs": {
            "enable_thinking": orcamento_pensamento > 0,
            "thinking_budget": orcamento_pensamento if orcamento_pensamento > 0 else 0
        }
    }

    # Diagnóstico: exibe resumo do payload apenas no modo verbose
    if modo_diagnostico == 2:
        print("\n[DIAGNÓSTICO] Payload enviado:")
        payload_diag = {k: v for k, v in payload.items() if k != "messages"}
        payload_diag["num_messages"] = len(payload["messages"])
        print(json.dumps(payload_diag, indent=2, ensure_ascii=False))

    print("\n=== RESPOSTA ===")
    if orcamento_pensamento > 0:
        print("🧠 [PENSAMENTO]:\n", end="", flush=True)
    else:
        print("⏳ Pensando...", end="", flush=True)

    resp = None
    try:
        resp = requests.post(
            config.get("api_url", "http://127.0.0.1:8080/v1/chat/completions"),
            json=payload,
            stream=True,
            timeout=config.get("timeout", 300),
        )
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        print(f"\n❌ Erro: Tempo limite da requisição excedido.")
        messages.pop()
        continue
    except requests.exceptions.HTTPError:
        status = resp.status_code if resp is not None else "?"
        texto_erro = resp.text if resp is not None else ""
        print(f"\n❌ Erro HTTP {status}: {texto_erro}")
        if modo_diagnostico >= 1 and resp is not None:
            print(f"[DIAG] Headers: {resp.headers}")
            print(f"[DIAG] Corpo completo: {resp.text}")
        messages.pop()
        continue
    except Exception as e:
        print(f"\n❌ Erro de conexão: {e}")
        messages.pop()
        continue

    resposta_visivel = ""
    cabecalho_resposta_impresso = False
    chunk_count = 0
    ultimo_chunk_timings = None

    for line in resp.iter_lines():
        if not line:
            continue

        line_str = line.decode("utf-8")
        if line_str.startswith("data: "):
            line_str = line_str[6:]
        if line_str.strip() == "[DONE]":
            break

        chunk_count += 1

        # Modo verbose: mostra o chunk bruto (limitado)
        if modo_diagnostico == 2 and line_str.strip():
            print(f"\n[DIAG] Chunk {chunk_count}: {line_str[:300]}{'...' if len(line_str) > 300 else ''}")

        # Modo normal: mostra indicador de progresso a cada 5 chunks
        if modo_diagnostico == 1 and chunk_count % 5 == 0:
            print(f"\r⏳ Recebendo... {chunk_count} chunks", end="", flush=True)

        try:
            chunk_data = json.loads(line_str)

            # Guarda timings para o resumo (vem no último chunk)
            if "timings" in chunk_data:
                ultimo_chunk_timings = chunk_data["timings"]

            # Verifica se o servidor enviou um erro no stream
            if "error" in chunk_data:
                erro_msg = chunk_data["error"].get("message", str(chunk_data["error"]))
                print(f"\n\n❌ Erro do servidor: {erro_msg}")
                resposta_visivel = ""  # invalida qualquer texto parcial
                break

            delta = chunk_data["choices"][0]["delta"]
            chunk_thinking = delta.get("reasoning_content") or ""
            chunk_text     = delta.get("content") or ""

            if chunk_thinking and orcamento_pensamento > 0:
                # Exibe pensamento em tempo real (não armazena)
                print(chunk_thinking, end="", flush=True)

            if chunk_text:
                if not cabecalho_resposta_impresso:
                    # Limpa a linha de progresso se existir
                    if modo_diagnostico == 1:
                        print("\r" + " " * 50, end="", flush=True)
                    if orcamento_pensamento > 0:
                        print("\n\n🤖 [RESPOSTA FINAL]:\n", end="", flush=True)
                    else:
                        print(f"\r🤖 [RESPOSTA]:{'':20}\n", end="", flush=True)
                    cabecalho_resposta_impresso = True
                print(chunk_text, end="", flush=True)
                resposta_visivel += chunk_text

        except (json.JSONDecodeError, KeyError, IndexError):
            continue

    # Exibe resumo final do diagnóstico (modos normal e verbose)
    if modo_diagnostico >= 1 and ultimo_chunk_timings:
        prompt_n = ultimo_chunk_timings.get("prompt_n", "?")
        predicted_n = ultimo_chunk_timings.get("predicted_n", "?")
        prompt_ms = ultimo_chunk_timings.get("prompt_ms", "?")
        predicted_ms = ultimo_chunk_timings.get("predicted_ms", "?")
        print(f"\n\n[DIAG] 📊 Tokens: prompt={prompt_n}, resposta={predicted_n}")
        print(f"[DIAG] ⏱️  Tempo: prompt={prompt_ms:.0f}ms, geração={predicted_ms:.0f}ms")

    if not cabecalho_resposta_impresso:
        print("\r⚠️  Sem resposta recebida.                    ")

    print("\n==========================")

    if resposta_visivel.strip():
        messages.append({"role": "assistant", "content": resposta_visivel.strip()})
    else:
        print("ℹ️  A resposta veio vazia. Sua mensagem anterior foi mantida no histórico.")