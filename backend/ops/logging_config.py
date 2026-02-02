"""
Structured logging configuration.

Provides JSON-formatted logs suitable for log aggregation systems
(ELK, Datadog, CloudWatch, etc.).

Configuration:
- Development: Human-readable console output
- Production: JSON lines to stdout

Environment variables:
- LOG_FORMAT: "json" or "console" (default: json in production)
- LOG_LEVEL: DEBUG, INFO, WARNING, ERROR (default: INFO)
"""
import logging
import os
import sys


def get_logging_config(debug: bool = False) -> dict:
    """
    Get Django LOGGING configuration.

    Args:
        debug: Whether running in debug mode

    Returns:
        Django LOGGING dict
    """
    log_level = os.environ.get("LOG_LEVEL", "INFO" if not debug else "DEBUG")
    log_format = os.environ.get("LOG_FORMAT", "console" if debug else "json")

    # Base configuration
    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "require_debug_false": {
                "()": "django.utils.log.RequireDebugFalse",
            },
            "require_debug_true": {
                "()": "django.utils.log.RequireDebugTrue",
            },
        },
    }

    if log_format == "json":
        config["formatters"] = {
            "json": {
                "()": "ops.logging_config.JsonFormatter",
            },
        }
        config["handlers"] = {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "stream": "ext://sys.stdout",
            },
            "null": {
                "class": "logging.NullHandler",
            },
        }
    else:
        config["formatters"] = {
            "verbose": {
                "format": "[{asctime}] {levelname} {name} {message}",
                "style": "{",
            },
            "simple": {
                "format": "{levelname} {message}",
                "style": "{",
            },
        }
        config["handlers"] = {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "verbose",
            },
            "null": {
                "class": "logging.NullHandler",
            },
        }

    config["loggers"] = {
        "": {
            "handlers": ["console"],
            "level": log_level,
        },
        "django": {
            "handlers": ["console"],
            "level": log_level,
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR" if not debug else log_level,
            "propagate": False,
        },
        "django.db.backends": {
            "handlers": ["console"] if debug else ["null"],
            "level": "DEBUG" if debug else "INFO",
            "propagate": False,
        },
        # Application loggers
        "accounts": {
            "handlers": ["console"],
            "level": log_level,
            "propagate": False,
        },
        "events": {
            "handlers": ["console"],
            "level": log_level,
            "propagate": False,
        },
        "projections": {
            "handlers": ["console"],
            "level": log_level,
            "propagate": False,
        },
        "tenant": {
            "handlers": ["console"],
            "level": log_level,
            "propagate": False,
        },
        "ops": {
            "handlers": ["console"],
            "level": log_level,
            "propagate": False,
        },
        "celery": {
            "handlers": ["console"],
            "level": log_level,
            "propagate": False,
        },
    }

    return config


class JsonFormatter(logging.Formatter):
    """
    JSON log formatter for structured logging.

    Outputs JSON lines with consistent fields:
    - timestamp: ISO 8601 timestamp
    - level: Log level name
    - logger: Logger name
    - message: Log message
    - extra: Any extra fields passed to the logger
    """

    def format(self, record: logging.LogRecord) -> str:
        import json
        from datetime import datetime

        # Base log entry
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add location info
        if record.pathname:
            log_entry["location"] = {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            }

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Add extra fields (anything beyond standard LogRecord attributes)
        standard_attrs = {
            "name", "msg", "args", "levelname", "levelno", "pathname",
            "filename", "module", "lineno", "funcName", "created",
            "msecs", "relativeCreated", "thread", "threadName",
            "processName", "process", "exc_info", "exc_text", "stack_info",
            "message",
        }

        extras = {}
        for key, value in record.__dict__.items():
            if key not in standard_attrs:
                try:
                    # Ensure value is JSON serializable
                    json.dumps(value)
                    extras[key] = value
                except (TypeError, ValueError):
                    extras[key] = str(value)

        if extras:
            log_entry["extra"] = extras

        return json.dumps(log_entry, default=str)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger with structured logging support.

    Usage:
        logger = get_logger(__name__)
        logger.info("User logged in", extra={"user_id": user.id, "company": company.slug})
    """
    return logging.getLogger(name)
