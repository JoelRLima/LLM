from .base import BaseSkill
from typing import Any, Dict
from datetime import datetime
from ddgs import DDGS
from logger import logger

class WebSearchSkill(BaseSkill):
    name = "web_search"
    description = "Realiza buscas na web usando DuckDuckGo. Retorna os títulos, links e snippets dos resultados. Use esta skill para obter informações atualizadas, como datas, notícias e fatos recentes."

    def get_schema(self) -> Dict[str, Any]:
        return {
            "query": "string (o termo de busca)",
            "max_results": "integer (opcional, padrão 3, máximo 10)"
        }

    def execute(self, args: Dict[str, Any]) -> Any:
        query = args.get("query")
        if not query:
            return {"ok": False, "done": False, "error": "Falta o argumento 'query'."}
        
        max_results = args.get("max_results", 3)
        
        # Cabeçalho com data/hora real — corrige a limitação do LLM de não saber a data atual
        now = datetime.now()
        header = (
            f"[CONTEXTO] Data e hora atual da máquina: {now.strftime('%A, %d de %B de %Y, %H:%M')} "
            f"(Fuso horário local do sistema)\n\n"
            f"[RESULTADOS DA BUSCA por '{query}']:\n"
        )
        
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
            
            if not results:
                return {"ok": True, "done": True, "data": header + "Nenhum resultado encontrado para esta busca."}
                
            formatted = []
            for r in results:
                formatted.append(f"Title: {r.get('title')}\nLink: {r.get('href')}\nSnippet: {r.get('body')}\n---")
                
            return {"ok": True, "done": True, "data": header + "\n".join(formatted)}
            
        except Exception as e:
            logger.error(f"WebSearchSkill error: {e}", exc_info=True)
            # Mesmo se a busca falhar, retorna a data atual que já é útil
            return {"ok": False, "done": False, "error": f"Falha na busca web: {e}. Nota: a data atual do sistema é {now.strftime('%d/%m/%Y')}."}
