import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logging(level=logging.INFO, logfile=None):
    root = logging.getLogger()
    root.setLevel(level)

    fmt = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    formatter = logging.Formatter(fmt)

    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(formatter)
    root.addHandler(ch)

    if logfile is None:
        logdir = os.getenv("LOG_DIR")
        if logdir:
            os.makedirs(logdir, exist_ok=True)
            logfile = os.path.join(logdir, "bot.log")

    if logfile:
        fh = RotatingFileHandler(logfile, maxBytes=10 * 1024 * 1024, backupCount=3)
        fh.setFormatter(formatter)
        fh.setLevel(level)
        root.addHandler(fh)


def get_logger(name):
    return logging.getLogger(name)


class LoggerManager:
    """Helper to setup logging once and provide contextual LoggerAdapter instances.

    Usage:
        lm = LoggerManager()
        lm.setup()
        log = lm.get_logger(__name__)
        clog = lm.get_context_logger(__name__, chat_id=123)
        clog.info("message")
    """

    def __init__(self):
        self._configured = False

    def setup(self, level=logging.INFO, logfile=None):
        if not self._configured:
            setup_logging(level=level, logfile=logfile)
            self._configured = True

    def get_logger(self, name: str):
        return get_logger(name)

    def get_context_logger(self, name: str, **extra):
        """Return a LoggerAdapter that injects `extra` into log records."""
        logger = get_logger(name)
        return logging.LoggerAdapter(logger, extra)


# module-level convenience
LOGGER_MANAGER = LoggerManager()

