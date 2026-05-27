import sys
from loguru import logger
from app.core.config import get_settings

def setup_logging() -> None:
    settings = get_settings()
    logger.remove()
    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )
    logger.add(sys.stdout, format=log_format, level=settings.log_level, colorize=True)
    logger.info(f"Logging initialised — level={settings.log_level}, env={settings.app_env}")