"""
Consolidador de fatos de segurança. NÃO usa LLM, NÃO executa ferramentas.
Apenas recebe resultados já obtidos e normaliza.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any
from agent.security_patterns import lookup


@dataclass
class Finding:
    pattern_id: str
    pattern: str
    location: str
    start_line: int
    end_line: int
    symbol: str
    snippet: str
    detection_method: str  # "ast" ou "grep"
    metadata: Dict[str, Any] = field(default_factory=dict)


# Mapeamento: tipo retornado pelo code_analyzer -> pattern_id
_TYPE_TO_PATTERN = {
    "subprocess.run": "PY001",
    "os.system": "PY002",
    "eval": "PY003",
    "exec": "PY004",
    "pickle.load": "PY005",
    "pickle.loads": "PY005",
    "yaml.load": "PY006",
    "hashlib.md5": "PY007",
    "hashlib.sha1": "PY008",
    "hardcoded_secret": "PY009",
    "os.path.join": "PY010",
    "sql_string_format": "PY011",
    "flask_debug_true": "PY012",
}


def _map_to_pattern_id(symbol: str) -> str:
    """Mapeia um símbolo de chamada para um pattern_id."""
    return _TYPE_TO_PATTERN.get(symbol, "PY999")  # PY999 = desconhecido


def consolidate(code_analyzer_result: dict, grep_results: list = None) -> List[Finding]:
    """
    Consolida resultados do code_analyzer (modo security) e do grep
    em uma lista normalizada de Findings.
    """
    findings = []
    seen = set()  # (location, start_line, pattern_id)

    if grep_results is None:
        grep_results = []

    # 1. Processa interesting_calls do code_analyzer
    for call in code_analyzer_result.get("interesting_calls", []):
        symbol = call.get("symbol", "")
        pid = _map_to_pattern_id(symbol)
        meta = lookup(pid)

        key = (call.get("location", code_analyzer_result.get("file", "")),
               call.get("line", 0), pid)
        if key in seen:
            continue
        seen.add(key)

        findings.append(Finding(
            pattern_id=pid,
            pattern=meta.get("pattern", symbol),
            location=call.get("location", code_analyzer_result.get("file", "")),
            start_line=call.get("line", 0),
            end_line=call.get("line", 0),
            symbol=symbol,
            snippet=call.get("snippet", "")[:120],
            detection_method="ast",
            metadata={
                "extra_args": call.get("extra_args", {}),
                "family": meta.get("family", ""),
                "cwe": meta.get("cwe", 0),
                "owasp": meta.get("owasp", ""),
                "why_interesting": meta.get("why_interesting", ""),
                "default_priority": meta.get("default_priority", 0),
            }
        ))

    # 2. Processa user_controlled_sources
    for src in code_analyzer_result.get("user_controlled_sources", []):
        key = (src.get("location", code_analyzer_result.get("file", "")),
               src.get("line", 0), "EXT001")
        if key in seen:
            continue
        seen.add(key)

        findings.append(Finding(
            pattern_id="EXT001",
            pattern="external_input",
            location=src.get("location", code_analyzer_result.get("file", "")),
            start_line=src.get("line", 0),
            end_line=src.get("line", 0),
            symbol=src.get("symbol", ""),
            snippet=src.get("snippet", "")[:120],
            detection_method="ast",
            metadata={"type": src.get("type", ""), "default_priority": 5}
        ))

    # 3. Processa grep_results
    for g in grep_results:
        key = (g.get("file", ""), g.get("line", 0), "PY009")
        if key in seen:
            continue
        seen.add(key)
        meta = lookup("PY009")

        findings.append(Finding(
            pattern_id="PY009",
            pattern="hardcoded_secret",
            location=g.get("file", ""),
            start_line=g.get("line", 0),
            end_line=g.get("line", 0),
            symbol="",
            snippet=g.get("content", "")[:120],
            detection_method="grep",
            metadata={
                "family": meta.get("family", ""),
                "cwe": meta.get("cwe", 0),
                "owasp": meta.get("owasp", ""),
                "why_interesting": meta.get("why_interesting", ""),
                "default_priority": meta.get("default_priority", 0),
            }
        ))

    # Ordena por default_priority decrescente
    findings.sort(key=lambda f: f.metadata.get("default_priority", 0), reverse=True)
    return findings