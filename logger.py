import logging
import sys

# Handlers globais para poder alterar o nível dinamicamente
_console_handler = None

def setup_logger(debug_mode: int = 0) -> logging.Logger:
    """
    Configura e retorna o logger principal da aplicação.
    """
    global _console_handler
    logger = logging.getLogger("LLM_Agent")
    
    if logger.hasHandlers():
        logger.handlers.clear()
        
    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    file_handler = logging.FileHandler("agent.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    _console_handler = logging.StreamHandler(sys.stdout)
    if debug_mode >= 1:
        _console_handler.setLevel(logging.DEBUG)
    else:
        _console_handler.setLevel(logging.WARNING)
        
    _console_handler.setFormatter(formatter)
    logger.addHandler(_console_handler)

    return logger

def set_debug_level(mode: int) -> None:
    """Ajusta o nível de debug no console em tempo real."""
    if _console_handler:
        if mode >= 1:
            _console_handler.setLevel(logging.DEBUG)
        else:
            _console_handler.setLevel(logging.WARNING)

logger = setup_logger()
