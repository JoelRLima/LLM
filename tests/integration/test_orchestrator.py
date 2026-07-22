from agent.parsers import extract_json, normalize_tool_result, validate_decision


def test_extract_json_puro():
    texto = '{"action": "final", "answer": "ola"}'
    res = extract_json(texto)
    assert res is not None
    assert res["action"] == "final"

def test_extract_json_com_markdown():
    texto = "Aqui está a resposta:\n```json\n{\"action\": \"tool\", \"tool\": \"t\"}\n```\nespero ter ajudado."
    res = extract_json(texto)
    assert res is not None
    assert res["action"] == "tool"

def test_extract_json_sujo():
    texto = "Vou usar a tool.\n{\"action\": \"tool\", \"tool\": \"t\"} \npronto!"
    res = extract_json(texto)
    assert res is not None
    assert res["action"] == "tool"

def test_extract_json_invalido():
    texto = "Nao tem json aqui { apenas chaves soltas"
    res = extract_json(texto)
    assert res is None

def test_validate_decision():
    # Valido
    v, err = validate_decision({"action": "final", "answer": "oi"})
    assert v is True
    assert err is None

    # Faltando campos
    v, err = validate_decision({"action": "final"})
    assert v is False
    assert "Falta" in err

    # Tool sem nome
    v, err = validate_decision({"action": "tool", "args": {}})
    assert v is False

    # Ação invalida
    v, err = validate_decision({"action": "bla"})
    assert v is False

def test_normalize_tool_result_ok():
    res = normalize_tool_result({"ok": True, "done": True, "data": "abc"}, [])
    assert res["ok"] is True
    assert res["done"] is True
    assert res["data"] == "abc"

def test_normalize_tool_result_string_sucesso():
    res = normalize_tool_result("tudo certo", [])
    assert res["ok"] is True
    assert res["data"] == "tudo certo"

def test_normalize_tool_result_string_erro():
    res = normalize_tool_result("arquivo nao encontrado", ["nao encontrado"])
    assert res["ok"] is False
    assert res["error"] == "arquivo nao encontrado"
