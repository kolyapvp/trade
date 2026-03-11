from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def configure_file_logging(log_dir: str, retention_days: int) -> None:
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    target_file = path / 'bot.log'
    for handler in root_logger.handlers:
        if isinstance(handler, TimedRotatingFileHandler) and Path(handler.baseFilename) == target_file:
            return

    file_handler = TimedRotatingFileHandler(
        filename=target_file,
        when='midnight',
        interval=1,
        backupCount=retention_days,
        encoding='utf-8',
    )
    file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
    root_logger.addHandler(file_handler)
