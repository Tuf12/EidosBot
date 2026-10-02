"""Remote Mobile & Telegram Gateway package for EidosBot."""

from eidos.gateway.server import RemoteGatewayServer, resolve_gateway_token
from eidos.gateway.telegram_bot import TelegramGateway, resolve_bot_token

__all__ = ["RemoteGatewayServer", "TelegramGateway", "resolve_gateway_token", "resolve_bot_token"]
