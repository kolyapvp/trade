import * as ccxt from 'ccxt';
import { IExchange, ExchangeInfo, Ticker, FuturesTicker } from '../../domain/ports/IExchange';
import { OrderBook, OrderBookLevel } from '../../domain/value-objects/OrderBook';
import { Fee } from '../../domain/value-objects/Fee';

export class CcxtExchangeAdapter implements IExchange {
  readonly info: ExchangeInfo;

  constructor(
    private readonly exchange: ccxt.Exchange,
    private readonly fee: Fee,
    private readonly supportsFutures: boolean = false,
  ) {
    this.info = {
      id: exchange.id,
      name: exchange.name ?? exchange.id,
      fee,
      supportsSpot: true,
      supportsFutures,
    };
  }

  async fetchOrderBook(symbol: string, limit: number = 20): Promise<OrderBook> {
    const raw = await this.exchange.fetchOrderBook(symbol, limit);

    const bids: OrderBookLevel[] = (raw.bids as number[][]).map(([price, qty]) => ({
      price,
      quantity: qty,
    }));
    const asks: OrderBookLevel[] = (raw.asks as number[][]).map(([price, qty]) => ({
      price,
      quantity: qty,
    }));

    return new OrderBook(symbol, this.exchange.id, bids, asks, raw.timestamp ?? Date.now());
  }

  async fetchTicker(symbol: string): Promise<Ticker> {
    const raw = await this.exchange.fetchTicker(symbol);
    return {
      symbol,
      exchangeId: this.exchange.id,
      bid: raw.bid ?? 0,
      ask: raw.ask ?? 0,
      last: raw.last ?? 0,
      volume: raw.baseVolume ?? 0,
      timestamp: raw.timestamp ?? Date.now(),
    };
  }

  async fetchTickers(symbols: string[]): Promise<Ticker[]> {
    const result: Ticker[] = [];

    if (this.exchange.has['fetchTickers']) {
      try {
        const raw = await this.exchange.fetchTickers(symbols);
        for (const symbol of symbols) {
          const t = raw[symbol];
          if (t) {
            result.push({
              symbol,
              exchangeId: this.exchange.id,
              bid: t.bid ?? 0,
              ask: t.ask ?? 0,
              last: t.last ?? 0,
              volume: t.baseVolume ?? 0,
              timestamp: t.timestamp ?? Date.now(),
            });
          }
        }
        return result;
      } catch {
      }
    }

    for (const symbol of symbols) {
      try {
        const t = await this.fetchTicker(symbol);
        result.push(t);
      } catch {
      }
    }

    return result;
  }

  async fetchFuturesTicker(symbol: string): Promise<FuturesTicker | null> {
    if (!this.supportsFutures) return null;

    try {
      const raw = await this.exchange.fetchTicker(symbol);
      const funding = await this.exchange.fetchFundingRate(symbol).catch(() => null);

      return {
        symbol,
        exchangeId: this.exchange.id,
        bid: raw.bid ?? 0,
        ask: raw.ask ?? 0,
        last: raw.last ?? 0,
        volume: raw.baseVolume ?? 0,
        timestamp: raw.timestamp ?? Date.now(),
        fundingRate: (funding as { fundingRate?: number })?.fundingRate ?? 0,
        nextFundingTime: (funding as { nextFundingDatetime?: number })?.nextFundingDatetime ?? 0,
        markPrice: (raw.info as Record<string, number>)?.markPrice ?? raw.last ?? 0,
        indexPrice: (raw.info as Record<string, number>)?.indexPrice ?? raw.last ?? 0,
      };
    } catch {
      return null;
    }
  }

  async isAvailable(): Promise<boolean> {
    try {
      await this.exchange.fetchStatus();
      return true;
    } catch {
      try {
        await this.exchange.fetchTime();
        return true;
      } catch {
        return false;
      }
    }
  }
}
