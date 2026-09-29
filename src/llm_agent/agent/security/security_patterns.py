"""
Banco de dados de padrões de segurança.

Este módulo NÃO contém lógica de detecção. Apenas metadados descritivos
sobre padrões que uma ferramenta de análise estática (ex.: code_analyzer)
pode ter observado no código-fonte. A decisão sobre severidade real,
falso-positivo/negativo etc. cabe a quem consome esses dados.
"""

PATTERN_DATABASE = {
    "PY001": {
        "pattern": "subprocess_shell_true",
        "family": "execution",
        "cwe": 78,
        "owasp": "A03",
        "why_interesting": "Shell interpreta comandos; concatenação de entrada pode levar a injeção de comandos.",
        "default_priority": 10,
    },
    "PY002": {
        "pattern": "os_system",
        "family": "execution",
        "cwe": 78,
        "owasp": "A03",
        "why_interesting": "Executa comando via shell do sistema operacional; risco de injeção de comandos.",
        "default_priority": 10,
    },
    "PY003": {
        "pattern": "eval_call",
        "family": "execution",
        "cwe": 95,
        "owasp": "A03",
        "why_interesting": "Avalia código Python arbitrário em tempo de execução.",
        "default_priority": 10,
    },
    "PY004": {
        "pattern": "exec_call",
        "family": "execution",
        "cwe": 95,
        "owasp": "A03",
        "why_interesting": "Executa código Python arbitrário em tempo de execução.",
        "default_priority": 10,
    },
    "PY005": {
        "pattern": "pickle_load",
        "family": "deserialization",
        "cwe": 502,
        "owasp": "A08",
        "why_interesting": "Desserialização insegura pode permitir execução de código arbitrário.",
        "default_priority": 9,
    },
    "PY006": {
        "pattern": "yaml_load_unsafe",
        "family": "deserialization",
        "cwe": 502,
        "owasp": "A08",
        "why_interesting": "yaml.load sem SafeLoader pode instanciar objetos arbitrários.",
        "default_priority": 9,
    },
    "PY007": {
        "pattern": "hashlib_md5",
        "family": "weak_crypto",
        "cwe": 327,
        "owasp": "A02",
        "why_interesting": "MD5 é criptograficamente quebrado; inadequado para senhas ou integridade.",
        "default_priority": 6,
    },
    "PY008": {
        "pattern": "hashlib_sha1",
        "family": "weak_crypto",
        "cwe": 327,
        "owasp": "A02",
        "why_interesting": "SHA1 é considerado fraco para uso criptográfico sensível.",
        "default_priority": 6,
    },
    "PY009": {
        "pattern": "hardcoded_secret",
        "family": "secrets",
        "cwe": 798,
        "owasp": "A07",
        "why_interesting": "Credenciais/segredos embutidos no código-fonte podem vazar via repositório.",
        "default_priority": 8,
    },
    "PY010": {
        "pattern": "path_traversal_join",
        "family": "path_traversal",
        "cwe": 22,
        "owasp": "A01",
        "why_interesting": "Junção de caminho com entrada não sanitizada pode escapar do diretório base.",
        "default_priority": 8,
    },
    "PY011": {
        "pattern": "sql_string_format",
        "family": "injection",
        "cwe": 89,
        "owasp": "A03",
        "why_interesting": "Construção de SQL via formatação/concatenação de string sugere risco de injeção.",
        "default_priority": 9,
    },
    "PY012": {
        "pattern": "flask_debug_true",
        "family": "misconfiguration",
        "cwe": 489,
        "owasp": "A05",
        "why_interesting": "Modo debug em produção pode expor console interativo e informações sensíveis.",
        "default_priority": 7,
    },
}


def lookup(pattern_id: str) -> dict:
    """Retorna os metadados do padrão ou {} se não encontrado."""
    return PATTERN_DATABASE.get(pattern_id, {})
