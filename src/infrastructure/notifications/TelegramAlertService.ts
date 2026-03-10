import * as https from 'https';
import { IAlertService, TradeAlert } from '../../domain/ports/IAlertService';

export class TelegramAlertService implements IAlertService {
  constructor(
    private readonly botToken: string,
    private readonly chatId: string,
  ) {}

  async sendTradeAlert(alert: TradeAlert): Promise<void> {
    if (!this.botToken || !this.chatId) return;
    const text = this.buildMessage(alert);
    await this.post(text);
  }

  private buildMessage(alert: TradeAlert): string {
    const icons: Record<string, string> = {
      cross_exchange: '⇄',
      triangular: '△',
      futures_spot: '◈',
    };
    const icon = icons[alert.strategy] ?? '●';
    const strategyLabel: Record<string, string> = {
      cross_exchange: 'МЕЖБИРЖЕВОЙ',
      triangular: 'ТРЕУГОЛЬНЫЙ',
      futures_spot: 'ФЬЮЧ-СПОТ',
    };
    const label = strategyLabel[alert.strategy] ?? alert.strategy.toUpperCase();

    return [
      `${icon} <b>Арбитраж — ${label}</b>`,
      `📊 Пара: <code>${alert.symbol}</code>`,
      `💹 Прибыль: <b>+${alert.profitPercent.toFixed(4)}%</b>  /  <b>+$${alert.profitUsdt.toFixed(4)}</b>`,
      `💰 Позиция: $${alert.positionUsdt}`,
      `ℹ️ ${alert.details}`,
      `🕐 ${alert.timestamp.toLocaleString('ru-RU')}`,
    ].join('\n');
  }

  private post(text: string): Promise<void> {
    return new Promise((resolve) => {
      const body = JSON.stringify({
        chat_id: this.chatId,
        text,
        parse_mode: 'HTML',
      });

      const req = https.request(
        {
          hostname: 'api.telegram.org',
          path: `/bot${this.botToken}/sendMessage`,
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Content-Length': Buffer.byteLength(body),
          },
        },
        (res) => {
          res.resume();
          resolve();
        },
      );

      req.on('error', () => resolve());
      req.write(body);
      req.end();
    });
  }
}
