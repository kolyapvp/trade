import { OrderBook } from '../value-objects/OrderBook';
import { Fee } from '../value-objects/Fee';

export interface Ticker {
  symbol: string;
  exchangeId: string;
  bid: number;
  ask: number;
  last: number;
  volume: number;
  timestamp: number;
}

export interface FuturesTicker extends Ticker {
  fundingRate: number;
  nextFundingTime: number;
  markPrice: number;
  indexPrice: number;
}

export interface ExchangeInfo {
  id: string;
  name: string;
  fee: Fee;
  supportsSpot: boolean;
  supportsFutures: boolean;
}

export interface IExchange {
  readonly info: ExchangeInfo;
  fetchOrderBook(symbol: string, limit?: number): Promise<OrderBook>;
  fetchTicker(symbol: string): Promise<Ticker>;
  fetchTickers(symbols: string[]): Promise<Ticker[]>;
  fetchFuturesTicker(symbol: string): Promise<FuturesTicker | null>;
  isAvailable(): Promise<boolean>;
}
