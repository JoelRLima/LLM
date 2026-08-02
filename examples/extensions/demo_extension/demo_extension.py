import json
import sys


def main() -> None:
    payload = json.loads(sys.stdin.read())
    tool_name = payload.get("tool")
    args = payload.get("args", {})
    text = args.get("text", "")

    if tool_name != "demo_tool":
        response = {
            "invocation_id": payload.get("invocation_id"),
            "status": "failed",
            "message": f"Ferramenta desconhecida: {tool_name}",
        }
        print(json.dumps(response))
        return

    response = {
        "invocation_id": payload.get("invocation_id"),
        "status": "succeeded",
        "message": f"Extensão demo respondeu: {text}",
        "data": {"echo": text},
    }
    print(json.dumps(response))


if __name__ == "__main__":
    main()
