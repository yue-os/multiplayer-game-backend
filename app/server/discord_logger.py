import os
import logging
import requests

class DiscordWebhookHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.webhook_url = os.getenv("DISCORD_WEBHOOK_URL")

    def emit(self, record):
        if not self.webhook_url:
            return

        if record.levelno < logging.WARNING:
            return

        try:
            log_entry = self.format(record)
            
            # Format differently based on severity
            if record.levelno >= logging.ERROR:
                content = f"🚨 **ERROR** 🚨\n```python\n{log_entry}\n```"
            else:
                content = f"⚠️ **WARNING** ⚠️\n```python\n{log_entry}\n```"

            # Discord has a 2000 character limit per message
            payload = {"content": content[:2000]} 
            requests.post(self.webhook_url, json=payload, timeout=5)
            
        except Exception:
            # If sending to Discord fails, just pass so we don't crash the server
            pass