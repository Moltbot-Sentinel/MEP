import json
import logging
import logging.handlers
import os
from datetime import datetime, timezone


LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)


class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra_fields"):
            log_data.update(record.extra_fields)
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)


def setup_logger(name: str, log_file: str, level=logging.INFO, json_format=True) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if logger.handlers:
        return logger
    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(LOG_DIR, log_file),
        maxBytes=10 * 1024 * 1024,
        backupCount=30,
    )
    if json_format:
        file_handler.setFormatter(JSONFormatter())
    else:
        file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logger.addHandler(file_handler)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(console_handler)
    return logger


hub_logger = setup_logger("mep.hub", "hub.json")
audit_logger = setup_logger("mep.audit", "ledger_audit.log", json_format=False)


def log_event(event: str, message: str, **kwargs):
    hub_logger.info(message, extra={"extra_fields": {"event": event, **kwargs}})


def log_audit(action: str, node_id: str, amount: float, new_balance: float, ref_id: str = ""):
    sign = "+" if amount >= 0 else ""
    audit_logger.info(
        f"AUDIT | {action} | Node: {node_id} | Amount: {sign}{amount:.6f} | Balance: {new_balance:.6f} | Ref: {ref_id}"
    )
