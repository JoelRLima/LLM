"""Canonical registry of security-relevant Python symbols."""

from __future__ import annotations

SecuritySymbol = tuple[str, str | None]

SECURITY_SYMBOL_REGISTRY: dict[str, SecuritySymbol] = {
    "request.args": ("source", None),
    "request.form": ("source", None),
    "request.json": ("source", None),
    "request.data": ("source", None),
    "request.values": ("source", None),
    "request.cookies": ("source", None),
    "request.headers": ("source", None),
    "request.GET": ("source", None),
    "request.POST": ("source", None),
    "request.files": ("source", None),
    "os.environ": ("source", None),
    "os.getenv": ("source", None),
    "sys.argv": ("source", None),
    "input": ("source", None),
    "eval": ("execution", "PY003"),
    "exec": ("execution", "PY004"),
    "compile": ("execution", None),
    "__import__": ("execution", None),
    "subprocess.run": ("execution", "PY001"),
    "subprocess.call": ("execution", None),
    "subprocess.Popen": ("execution", None),
    "subprocess.check_call": ("execution", None),
    "subprocess.check_output": ("execution", None),
    "os.system": ("execution", "PY002"),
    "os.popen": ("execution", None),
    "os.execv": ("execution", None),
    "os.execve": ("execution", None),
    "pickle.load": ("execution", "PY005"),
    "pickle.loads": ("execution", "PY005"),
    "yaml.load": ("execution", "PY006"),
    "marshal.loads": ("execution", None),
    "open": ("filesystem", None),
    "os.remove": ("filesystem", None),
    "os.unlink": ("filesystem", None),
    "os.rename": ("filesystem", None),
    "os.rmdir": ("filesystem", None),
    "shutil.rmtree": ("filesystem", None),
    "shutil.copy": ("filesystem", None),
    "shutil.move": ("filesystem", None),
    "os.path.join": ("filesystem", "PY010"),
    "pathlib.Path": ("filesystem", None),
    "requests.get": ("network", None),
    "requests.post": ("network", None),
    "requests.put": ("network", None),
    "requests.delete": ("network", None),
    "requests.request": ("network", None),
    "urllib.request.urlopen": ("network", None),
    "urlopen": ("network", None),
    "socket.socket": ("network", None),
    "http.client.HTTPConnection": ("network", None),
    "hashlib.md5": ("crypto", "PY007"),
    "hashlib.sha1": ("crypto", "PY008"),
    "hashlib.sha256": ("crypto", None),
    "hashlib.new": ("crypto", None),
    "Crypto.Cipher.AES.new": ("crypto", None),
    "cryptography.fernet.Fernet": ("crypto", None),
    "jwt.encode": ("auth", None),
    "jwt.decode": ("auth", None),
    "bcrypt.hashpw": ("auth", None),
    "bcrypt.checkpw": ("auth", None),
    "passlib.hash": ("auth", None),
    "check_password_hash": ("auth", None),
    "generate_password_hash": ("auth", None),
}


def symbols_for(category: str) -> set[str]:
    return {symbol for symbol, (registered_category, _) in SECURITY_SYMBOL_REGISTRY.items() if registered_category == category}


def get_pattern_id_map() -> dict[str, str]:
    return {
        symbol: pattern_id
        for symbol, (_, pattern_id) in SECURITY_SYMBOL_REGISTRY.items()
        if pattern_id is not None
    }
