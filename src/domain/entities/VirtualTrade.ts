import { ArbitrageStrategy, StrategyDetails } from './ArbitrageOpportunity';

export type TradeStatus = 'open' | 'closed' | 'failed';

export class VirtualTrade {
  public readonly id: string;
  public readonly openedAt: Date;
  public closedAt?: Date;
  public status: TradeStatus;
  public actualProfitUsdt?: number;
  public notes?: string;

  constructor(
    public readonly strategy: ArbitrageStrategy,
    public readonly symbol: string,
    public readonly positionSizeUsdt: number,
    public readonly expectedProfitUsdt: number,
    public readonly expectedProfitPercent: number,
    public readonly details: StrategyDetails,
  ) {
    this.id = `vtrade-${strategy}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    this.openedAt = new Date();
    this.status = 'open';
  }

  close(actualProfitUsdt: number, notes?: string): void {
    this.closedAt = new Date();
    this.status = 'closed';
    this.actualProfitUsdt = actualProfitUsdt;
    this.notes = notes;
  }

  fail(reason: string): void {
    this.closedAt = new Date();
    this.status = 'failed';
    this.notes = reason;
  }

  get holdingTimeMs(): number {
    const end = this.closedAt ?? new Date();
    return end.getTime() - this.openedAt.getTime();
  }

  toJSON(): Record<string, unknown> {
    return {
      id: this.id,
      strategy: this.strategy,
      symbol: this.symbol,
      positionSizeUsdt: this.positionSizeUsdt,
      expectedProfitUsdt: this.expectedProfitUsdt,
      expectedProfitPercent: this.expectedProfitPercent,
      actualProfitUsdt: this.actualProfitUsdt,
      status: this.status,
      openedAt: this.openedAt.toISOString(),
      closedAt: this.closedAt?.toISOString(),
      holdingTimeMs: this.holdingTimeMs,
      notes: this.notes,
      details: this.details,
    };
  }
}
