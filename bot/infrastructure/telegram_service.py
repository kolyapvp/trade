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
        if alert.alert_type == 'closed':
            return self._build_closed_message(alert)
        return self._build_opened_message(alert)

    def _mode_label(self, alert: TradeAlert) -> str:
        return 'LIVE' if alert.mode == 'real' else 'DEMO'

    def _build_opened_message(self, alert: TradeAlert) -> str:
        icons = {'cross_exchange': '⇄', 'triangular': '△', 'futures_spot': '◈', 'futures_funding': '⟐'}
        labels = {'cross_exchange': 'МЕЖБИРЖЕВОЙ', 'triangular': 'ТРЕУГОЛЬНЫЙ', 'futures_spot': 'ФЬЮЧ-СПОТ', 'futures_funding': 'ФАНДИНГ'}
        icon = icons.get(alert.strategy, '●')
        label = labels.get(alert.strategy, alert.strategy.upper())
        mode_label = self._mode_label(alert)

        profit_emoji = '🟢' if alert.profit_usdt > 0 else '🔴'
        hour_sign = '+' if alert.profit_last_hour >= 0 else ''
        day_sign = '+' if alert.profit_last_24h >= 0 else ''

        lines = [
            f'{icon} <b>[{mode_label}] Арбитраж — {label}</b>',
            f'📊 Пара: <code>{alert.symbol}</code>',
            f'{profit_emoji} Ожид. прибыль: <b>+{alert.profit_percent:.4f}%</b>  /  <b>+${alert.profit_usdt:.4f}</b>',
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

    def _build_closed_message(self, alert: TradeAlert) -> str:
        profit = alert.profit_usdt
        mode_label = self._mode_label(alert)
        profit_emoji = '✅' if profit >= 0 else '❌'
        profit_sign = '+' if profit >= 0 else ''
        pct_sign = '+' if alert.profit_percent >= 0 else ''
        hour_sign = '+' if alert.profit_last_hour >= 0 else ''
        day_sign = '+' if alert.profit_last_24h >= 0 else ''

        hours = alert.hours_held or 0.0
        total_seconds = int(hours * 3600)
        h = total_seconds // 3600
        m = (total_seconds % 3600) // 60
        s = total_seconds % 60
        if h > 0:
            duration_str = f'{h}ч {m}мин'
        elif m > 0:
            duration_str = f'{m}мин {s}сек'
        else:
            duration_str = f'{s}сек'

        labels = {'futures_spot': 'ФЬЮЧ-СПОТ', 'futures_funding': 'ФАНДИНГ'}
        title = labels.get(alert.strategy, alert.strategy.upper())
        marker = '⟐' if alert.strategy == 'futures_funding' else '◈'
        lines = [
            f'{marker} <b>[{mode_label}] {title} — ПОЗИЦИЯ ЗАКРЫТА</b>',
            f'📊 Пара: <code>{alert.symbol}</code>',
            f'{profit_emoji} P&L: <b>{profit_sign}${profit:.4f}</b>  ({pct_sign}{alert.profit_percent:.4f}%)',
            f'⏱ Держалась: <b>{duration_str}</b>',
            '',
        ]

        if alert.entry_spot_price is not None and alert.entry_futures_price is not None:
            entry_basis = alert.entry_basis_percent or 0.0
            exit_basis = alert.exit_basis_percent or 0.0
            if alert.strategy == 'futures_funding':
                lines += [
                    f'📌 Вход: long ${alert.entry_spot_price:.4f} / short ${alert.entry_futures_price:.4f}  (спред {entry_basis:+.4f}%)',
                    f'📌 Выход: long ${alert.exit_spot_price:.4f} / short ${alert.exit_futures_price:.4f}  (спред {exit_basis:+.4f}%)',
                ]
            else:
                lines += [
                    f'📌 Вход: спот ${alert.entry_spot_price:.4f} / фьюч ${alert.entry_futures_price:.4f}  (базис {entry_basis:+.4f}%)',
                    f'📌 Выход: спот ${alert.exit_spot_price:.4f} / фьюч ${alert.exit_futures_price:.4f}  (базис {exit_basis:+.4f}%)',
                ]

        if alert.close_reason:
            lines.append(f'🔄 Причина: {alert.close_reason}')

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
