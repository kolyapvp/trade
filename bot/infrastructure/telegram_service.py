from __future__ import annotations

import asyncio
import json as json_module
import urllib.request
import urllib.error
from datetime import datetime

from ..domain.ports import IAlertService, TradeAlert


class TelegramAlertService(IAlertService):
    def __init__(self, bot_token: str, chat_id: str):
        self._bot_token = bot_token
        self._chat_id = chat_id

    async def send_trade_alert(self, alert: TradeAlert) -> None:
        if not self._bot_token or not self._chat_id:
            return
        text = self._build_message(alert)
        await asyncio.to_thread(self._post, text)

    def _build_message(self, alert: TradeAlert) -> str:
        icons = {
            'cross_exchange': '⇄',
            'triangular': '△',
            'futures_spot': '◈',
        }
        labels = {
            'cross_exchange': 'МЕЖБИРЖЕВОЙ',
            'triangular': 'ТРЕУГОЛЬНЫЙ',
            'futures_spot': 'ФЬЮЧ-СПОТ',
        }
        icon = icons.get(alert.strategy, '●')
        label = labels.get(alert.strategy, alert.strategy.upper())

        profit_emoji = '🟢' if alert.profit_usdt > 0 else '🔴'
        hour_sign = '+' if alert.profit_last_hour >= 0 else ''
        day_sign = '+' if alert.profit_last_24h >= 0 else ''

        lines = [
            f'{icon} <b>Арбитраж — {label}</b>',
            f'📊 Пара: <code>{alert.symbol}</code>',
            f'{profit_emoji} Прибыль: <b>+{alert.profit_percent:.4f}%</b>  /  <b>+${alert.profit_usdt:.4f}</b>',
            f'💰 Позиция: ${alert.position_usdt:.0f}',
        ]

        if alert.workflow:
            lines.append('')
            lines.append('📋 <b>Как работает сделка:</b>')
            lines.extend(alert.workflow)

        hour_str = f'{hour_sign}${alert.profit_last_hour:.4f}'
        day_str = f'{day_sign}${alert.profit_last_24h:.4f}'
        lines += [
            '',
            f'📈 За час: <b>{hour_str}</b>  |  За 24ч: <b>{day_str}</b>',
            f'🕐 {alert.timestamp.strftime("%d.%m.%Y %H:%M:%S")}',
        ]
        return '\n'.join(lines)

    def _post(self, text: str) -> None:
        body = json_module.dumps({
            'chat_id': self._chat_id,
            'text': text,
            'parse_mode': 'HTML',
        }).encode('utf-8')
        req = urllib.request.Request(
            f'https://api.telegram.org/bot{self._bot_token}/sendMessage',
            data=body,
            headers={'Content-Type': 'application/json'},
        )
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception:
            pass
