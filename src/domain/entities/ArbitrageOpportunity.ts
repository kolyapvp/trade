export type ArbitrageStrategy = 'cross_exchange' | 'triangular' | 'futures_spot';

export interface CrossExchangeDetails {
  buyExchange: string;
  sellExchange: string;
  buyPrice: number;
  sellPrice: number;
  buyFee: number;
  sellFee: number;
  maxQty: number;
  symbol: string;
}

export interface TriangularDetails {
  exchange: string;
  path: string[];
  startAmount: number;
  endAmount: number;
  fees: number;
}

export interface FuturesSpotDetails {
  exchange: string;
  symbol: string;
  spotPrice: number;
  futuresPrice: number;
  fundingRate: number;
  basis: number;
  basisPercent: number;
}

export type StrategyDetails = CrossExchangeDetails | TriangularDetails | FuturesSpotDetails;

export class ArbitrageOpportunity {
  public readonly id: string;
  public readonly detectedAt: Date;

  constructor(
    public readonly strategy: ArbitrageStrategy,
    public readonly symbol: string,
    public readonly profitUsdt: number,
    public readonly profitPercent: number,
    public readonly positionSizeUsdt: number,
    public readonly details: StrategyDetails,
  ) {
    this.id = `${strategy}-${symbol}-${Date.now()}`;
    this.detectedAt = new Date();
  }

  isProfitable(minProfitPercent: number): boolean {
    return this.profitPercent >= minProfitPercent && this.profitUsdt > 0;
  }

  toString(): string {
    return `[${this.strategy.toUpperCase()}] ${this.symbol} | +${this.profitPercent.toFixed(4)}% | $${this.profitUsdt.toFixed(4)}`;
  }
}
