"""
agent/skills/safe_path.py

SafePathResolver — utilitário único de resolução segura de caminhos
dentro de um diretório base (achado crítico 1.3).

Antes deste PR, 4 skills (grep.py, directory_reader.py, code_analyzer.py,
file_reader.py) validavam o acesso ao diretório seguro com:

    str(requested).startswith(str(self.base_dir))

Essa checagem é falha: `/home/user/projeto` e `/home/user/projeto_secreto`
"começam com" `/home/user/projeto` como string, sem respeitar limite de
diretório — um path traversal ainda seria detectado corretamente na
maioria dos casos óbvios (ex.: `../../etc/passwd`, que o `.resolve()` já
normaliza para fora do prefixo), mas a comparação por string abre brecha
para diretórios irmãos cujo nome estende o do diretório base.

`file_writer.py` já usava a forma correta (`Path.relative_to()` dentro de
um `try/except ValueError`); este módulo generaliza esse padrão para ser
reaproveitado pelas 4 skills afetadas.
"""
from pathlib import Path
from typing import Optional, Tuple


def resolve_safe_path(base_dir: Path, relative_path: str) -> Tuple[Optional[Path], Optional[str]]:
    """
    Resolve `relative_path` dentro de `base_dir` com segurança.

    Retorna uma tupla (caminho_resolvido, erro):
      - Em caso de sucesso: (Path resolvido, None).
      - Em caso de falha (caminho inválido ou fora do diretório seguro):
        (None, mensagem de erro).

    Usa `Path.relative_to()` — que lança `ValueError` se `requested` não
    estiver de fato dentro de `base_dir` — em vez de comparação de string
    por prefixo (`startswith`), fechando a brecha do achado 1.3.
    """
    try:
        requested = (base_dir / relative_path).resolve()
    except Exception as e:
        return None, f"Caminho inválido: {relative_path} ({e})"

    try:
        requested.relative_to(base_dir)
    except ValueError:
        return None, f"Acesso fora do diretório seguro: {relative_path}"

    return requested, None
