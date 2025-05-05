import logging.config

from yaacs.cli import main

logging.config.dictConfig(
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"simple": {"format": "%(message)s"}},
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "formatter": "simple",
                "stream": "ext://sys.stdout",
            }
        },
        "loggers": {"root": {"level": "WARNING", "handlers": ["stdout"]}},
    }
)
if __name__ == "__main__":
    main()
