import os

# Lista exata de tudo o que você quer ignorar
IGNORAR = {
    "config.json", ".venv", "__pycache__", "agent_memory.json", 
    "agent.log", ".temp_analysis", "analysis_notes.md", 
    "memory_backups", "agent_metrics.jsonl", ".github", 
    ".agents", ".pytest_cache", ".ruff_cache", ".git",
    "estrutura.txt", "estrutura.txtfind", "EstruturaProjeto.md"
}

def listar_arvore(diretorio, f_txt, prefixo=""):
    try:
        itens = sorted(os.listdir(diretorio))
    except PermissionError:
        return

    # Filtra os itens ignorando os nomes exatos e extensões .pyc
    itens_filtrados = [
        i for i in itens 
        if i not in IGNORAR and not i.endswith('.pyc') and i != "gerar.py"
    ]

    for q, item in enumerate(itens_filtrados):
        caminho_completo = os.path.join(diretorio, item)
        eh_ultimo = (q == len(itens_filtrados) - 1)
        
        # Define os caracteres da árvore
        marcador = "└── " if eh_ultimo else "├── "
        f_txt.write(f"{prefixo}{marcador}{item}\n")
        
        # Se for uma pasta, entra nela recursivamente
        if os.path.isdir(caminho_completo):
            proximo_prefixo = prefixo + ("    " if eh_ultimo else "│   ")
            listar_arvore(caminho_completo, f_txt, proximo_prefixo)

# Executa e grava o arquivo
with open("estrutura.txt", "w", encoding="utf-8") as f:
    f.write(".\n")
    listar_arvore(".", f)

print("Arquivo estrutura.txt gerado com sucesso!")
