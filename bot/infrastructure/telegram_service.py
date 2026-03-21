from __future__ import annotations

import asyncio
import json as json_module
import logging
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from datetime import datetime

from ..domain.ports import IAlertService, TradeAlert

logger = logging.getLogger(__name__)


class TelegramAlertService(IAlertService):
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        api_base_url: str = 'https://api.telegram.org',
        api_host_override: str = '',
        insecure_ssl: bool = False,
    ):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._api_base_url = api_base_url.rstrip('/')
        self._api_host_override = api_host_override.strip()
        self._ssl_context = self._build_ssl_context(insecure_ssl)

    async def send_trade_alert(self, alert: TradeAlert) -> None:
        if not self._bot_token or not self._chat_id:
            return
        text = self._build_message(alert)
        await asyncio.to_thread(self._post, text)

    async def send_text_alert(self, text: str) -> None:
        if not self._bot_token or not self._chat_id:
            return
        await asyncio.to_thread(self._post, text)

    def _build_message(self, alert: TradeAlert) -> str:
        if alert.alert_type == 'closed':
            return self._build_closed_message(alert)
        if alert.alert_type == 'signal':
            return self._build_signal_message(alert)
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

        if alert.details:
            lines.append('')
            lines.append('📎 <b>Детали:</b>')
            lines.extend(alert.details.splitlines())

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

    def _build_signal_message(self, alert: TradeAlert) -> str:
        icons = {'cross_exchange': '⇄', 'triangular': '△', 'futures_spot': '◈', 'futures_funding': '⟐'}
        labels = {'cross_exchange': 'МЕЖБИРЖЕВОЙ', 'triangular': 'ТРЕУГОЛЬНЫЙ', 'futures_spot': 'ФЬЮЧ-СПОТ', 'futures_funding': 'ФАНДИНГ'}
        icon = icons.get(alert.strategy, '●')
        label = labels.get(alert.strategy, alert.strategy.upper())
        mode_label = self._mode_label(alert)

        lines = [
            f'{icon} <b>[{mode_label}] Сигнал — {label}</b>',
            f'📊 Пара: <code>{alert.symbol}</code>',
            f'🟢 Ожид. прибыль: <b>+{alert.profit_percent:.4f}%</b>  /  <b>+${alert.profit_usdt:.4f}</b>',
            f'💰 Расчётная позиция: ${alert.position_usdt:.0f}',
        ]

        if alert.details:
            lines.append('')
            lines.append('📎 <b>Детали:</b>')
            lines.extend(alert.details.splitlines())

        if alert.workflow:
            lines.append('')
            lines.append('📋 <b>Логика сделки:</b>')
            lines.extend(alert.workflow)

        lines += [
            '',
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
        headers = {'Content-Type': 'application/json'}
        if self._api_host_override:
            headers['Host'] = self._api_host_override
        req = urllib.request.Request(
            f'{self._api_base_url}/bot{self._bot_token}/sendMessage',
            data=body,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=10, context=self._ssl_context) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            response_body = exc.read().decode('utf-8', errors='replace')
            logger.warning('telegram_send_failed status=%s body=%s', exc.code, response_body)
        except urllib.error.URLError as exc:
            logger.warning('telegram_send_failed reason=%s', exc.reason)
        except Exception:
            logger.exception('telegram_send_failed')

    def _build_ssl_context(self, insecure_ssl: bool) -> ssl.SSLContext | None:
        if not insecure_ssl:
            return None
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        target_host = urlsplit(self._api_base_url).hostname or ''
        logger.warning(
            'telegram_insecure_ssl_enabled api_host=%s host_override=%s',
            target_host,
            self._api_host_override,
        )
        return context
