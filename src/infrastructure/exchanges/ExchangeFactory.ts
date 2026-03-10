import * as ccxt from 'ccxt';
import { CcxtExchangeAdapter } from './CcxtExchangeAdapter';
import { Fee } from '../../domain/value-objects/Fee';
import { ExchangeCredentials } from '../../config/config';

export class ExchangeFactory {
  createBinanceSpot(creds?: ExchangeCredentials): CcxtExchangeAdapter {
    const instance = new ccxt.binance({
      apiKey: creds?.apiKey || undefined,
      secret: creds?.secret || undefined,
      options: { defaultType: 'spot' },
      enableRateLimit: true,
    });
    return new CcxtExchangeAdapter(instance, Fee.binance(), false);
  }

  createBinanceFutures(creds?: ExchangeCredentials): CcxtExchangeAdapter {
    const instance = new ccxt.binance({
      apiKey: creds?.apiKey || undefined,
      secret: creds?.secret || undefined,
      options: { defaultType: 'future' },
      enableRateLimit: true,
    });
    return new CcxtExchangeAdapter(instance, new Fee(0.0002, 0.0004), true);
  }

  createBybit(creds?: ExchangeCredentials): CcxtExchangeAdapter {
    const instance = new ccxt.bybit({
      apiKey: creds?.apiKey || undefined,
      secret: creds?.secret || undefined,
      options: { defaultType: 'spot' },
      enableRateLimit: true,
    });
    return new CcxtExchangeAdapter(instance, Fee.bybit(), false);
  }

  createBybitFutures(creds?: ExchangeCredentials): CcxtExchangeAdapter {
    const instance = new ccxt.bybit({
      apiKey: creds?.apiKey || undefined,
      secret: creds?.secret || undefined,
      options: { defaultType: 'linear' },
      enableRateLimit: true,
    });
    return new CcxtExchangeAdapter(instance, new Fee(0.0001, 0.0006), true);
  }

  createOkx(creds?: ExchangeCredentials): CcxtExchangeAdapter {
    const instance = new ccxt.okx({
      apiKey: creds?.apiKey || undefined,
      secret: creds?.secret || undefined,
      password: creds?.passphrase || undefined,
      options: { defaultType: 'spot' },
      enableRateLimit: true,
    });
    return new CcxtExchangeAdapter(instance, Fee.okx(), false);
  }

  createKucoin(creds?: ExchangeCredentials): CcxtExchangeAdapter {
    const instance = new ccxt.kucoin({
      apiKey: creds?.apiKey || undefined,
      secret: creds?.secret || undefined,
      password: creds?.passphrase || undefined,
      enableRateLimit: true,
    });
    return new CcxtExchangeAdapter(instance, Fee.kucoin(), false);
  }

  createGateio(creds?: ExchangeCredentials): CcxtExchangeAdapter {
    const instance = new ccxt.gateio({
      apiKey: creds?.apiKey || undefined,
      secret: creds?.secret || undefined,
      enableRateLimit: true,
    });
    return new CcxtExchangeAdapter(instance, Fee.gateio(), false);
  }

  createMexc(creds?: ExchangeCredentials): CcxtExchangeAdapter {
    const instance = new ccxt.mexc({
      apiKey: creds?.apiKey || undefined,
      secret: creds?.secret || undefined,
      enableRateLimit: true,
    });
    return new CcxtExchangeAdapter(instance, Fee.mexc(), false);
  }

  createBitget(creds?: ExchangeCredentials): CcxtExchangeAdapter {
    const instance = new ccxt.bitget({
      apiKey: creds?.apiKey || undefined,
      secret: creds?.secret || undefined,
      password: creds?.passphrase || undefined,
      enableRateLimit: true,
    });
    return new CcxtExchangeAdapter(instance, Fee.bitget(), false);
  }

  createHtx(creds?: ExchangeCredentials): CcxtExchangeAdapter {
    const instance = new ccxt.htx({
      apiKey: creds?.apiKey || undefined,
      secret: creds?.secret || undefined,
      enableRateLimit: true,
    });
    return new CcxtExchangeAdapter(instance, Fee.htx(), false);
  }
}
