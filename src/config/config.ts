import * as dotenv from 'dotenv';
import * as path from 'path';

dotenv.config({ path: path.resolve(process.cwd(), '.env') });

export type BotMode = 'demo' | 'live' | 'report';

export interface ExchangeCredentials {
  apiKey: string;
  secret: string;
  passphrase?: string;
}

export interface AppConfig {
  mode: BotMode;
  scanIntervalMs: number;
  minProfitPercent: number;
  maxPositionUsdt: number;
  logFile: string;
  exchanges: {
    binance: ExchangeCredentials;
    bybit: ExchangeCredentials;
    okx: ExchangeCredentials;
    kucoin: ExchangeCredentials;
    gateio: ExchangeCredentials;
    mexc: ExchangeCredentials;
    bitget: ExchangeCredentials;
    htx: ExchangeCredentials;
  };
  telegram: {
    botToken: string;
    chatId: string;
  };
  pairs: string[];
  strategies: {
    crossExchange: boolean;
    triangular: boolean;
    futuresSpot: boolean;
  };
}

const args = process.argv.slice(2);
const modeArg = args.find((a) => a.startsWith('--mode='));
const modeFromArgs = modeArg ? (modeArg.split('=')[1] as BotMode) : undefined;

export const config: AppConfig = {
  mode: modeFromArgs ?? ((process.env.MODE as BotMode) ?? 'demo'),
  scanIntervalMs: parseInt(process.env.SCAN_INTERVAL_MS ?? '3000', 10),
  minProfitPercent: parseFloat(process.env.MIN_PROFIT_PERCENT ?? '0.1'),
  maxPositionUsdt: parseFloat(process.env.MAX_POSITION_USDT ?? '100'),
  logFile: process.env.LOG_FILE ?? 'trades.json',
  exchanges: {
    binance: {
      apiKey: process.env.BINANCE_API_KEY ?? '',
      secret: process.env.BINANCE_SECRET ?? '',
    },
    bybit: {
      apiKey: process.env.BYBIT_API_KEY ?? '',
      secret: process.env.BYBIT_SECRET ?? '',
    },
    okx: {
      apiKey: process.env.OKX_API_KEY ?? '',
      secret: process.env.OKX_SECRET ?? '',
      passphrase: process.env.OKX_PASSPHRASE ?? '',
    },
    kucoin: {
      apiKey: process.env.KUCOIN_API_KEY ?? '',
      secret: process.env.KUCOIN_SECRET ?? '',
      passphrase: process.env.KUCOIN_PASSPHRASE ?? '',
    },
    gateio: {
      apiKey: process.env.GATEIO_API_KEY ?? '',
      secret: process.env.GATEIO_SECRET ?? '',
    },
    mexc: {
      apiKey: process.env.MEXC_API_KEY ?? '',
      secret: process.env.MEXC_SECRET ?? '',
    },
    bitget: {
      apiKey: process.env.BITGET_API_KEY ?? '',
      secret: process.env.BITGET_SECRET ?? '',
      passphrase: process.env.BITGET_PASSPHRASE ?? '',
    },
    htx: {
      apiKey: process.env.HTX_API_KEY ?? '',
      secret: process.env.HTX_SECRET ?? '',
    },
  },
  telegram: {
    botToken: process.env.TELEGRAM_BOT_TOKEN ?? '',
    chatId: process.env.TELEGRAM_CHAT_ID ?? '',
  },
  pairs: [
    'BTC/USDT',
    'ETH/USDT',
    'SOL/USDT',
    'BNB/USDT',
    'XRP/USDT',
    'DOGE/USDT',
    'ADA/USDT',
    'AVAX/USDT',
    'DOT/USDT',
    'LINK/USDT',
    'LTC/USDT',
    'ATOM/USDT',
    'TRX/USDT',
    'UNI/USDT',
    'APT/USDT',
    'SUI/USDT',
    'ARB/USDT',
    'OP/USDT',
    'MATIC/USDT',
    'FIL/USDT',
    'NEAR/USDT',
    'INJ/USDT',
  ],
  strategies: {
    crossExchange: true,
    triangular: true,
    futuresSpot: true,
  },
};
