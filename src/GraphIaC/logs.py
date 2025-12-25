
import logging

import colorlog


def setup_logger(level=logging.INFO):
    logger = logging.getLogger("GraphIaC")
    logger.setLevel(level)
    logger.propagate = False

    # Only add handler once
    if not logger.handlers:
        handler = colorlog.StreamHandler()

        formatter = colorlog.ColoredFormatter(
            "%(log_color)s%(levelname)-8s%(reset)s | "
            "%(blue)s%(name)s: %(reset)s%(message_log_color)s%(message)s",
            reset=True,
            log_colors={
                "DEBUG":    "cyan",
                "INFO":     "bold_white",
                "PLAN":     "bold_green",
                "WARNING":  "bold_yellow",
                "ERROR":    "bold_red",
                "CRITICAL": "bg_red",
            },
            secondary_log_colors={
                "message": {
                    "DEBUG":    "cyan",
                    "INFO":     "white",
                    "WARNING":  "yellow",
                    "ERROR":    "red",
                    "CRITICAL": "red",
                }
            },
            style="%"
        )

        handler.setFormatter(formatter)
        logger.addHandler(handler)

        # Define custom level only once
        PLAN_LVL = 25
        logging.addLevelName(PLAN_LVL, "PLAN")

        def plan(self, message, *args, **kws):
            if self.isEnabledFor(PLAN_LVL):
                self._log(PLAN_LVL, message, args, **kws)

        logging.Logger.plan = plan

    return logger


