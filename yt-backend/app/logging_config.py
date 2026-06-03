import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_app_logging() -> str:
    """
    앱 로그를 yt-backend/server.log에 항상 남기도록 설정한다.

    uvicorn 실행 방식이나 stdout 리다이렉션 여부와 무관하게 파일 로그가
    기록되도록 Python logging FileHandler를 직접 붙인다.
    """

    backend_root = Path(__file__).resolve().parents[1]
    log_path = Path(os.getenv("APP_LOG_PATH", backend_root / "server.log"))
    if not log_path.is_absolute():
        log_path = backend_root / log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)

    level_name = os.getenv("APP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    resolved_log_path = str(log_path.resolve())
    for handler in root_logger.handlers:
        if getattr(handler, "_yt_analyzer_log_path", None) == resolved_log_path:
            return resolved_log_path

    max_bytes = int(os.getenv("APP_LOG_MAX_BYTES", str(10 * 1024 * 1024)))
    backup_count = int(os.getenv("APP_LOG_BACKUP_COUNT", "5"))
    file_handler = RotatingFileHandler(
        resolved_log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    file_handler._yt_analyzer_log_path = resolved_log_path
    root_logger.addHandler(file_handler)

    logging.getLogger(__name__).info("파일 로그 초기화 완료: %s", resolved_log_path)
    return resolved_log_path
