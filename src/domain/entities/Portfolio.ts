import { VirtualTrade } from './VirtualTrade';

export class Portfolio {
  private readonly trades: VirtualTrade[] = [];
  private totalVirtualCapital: number;

  constructor(initialCapital: number = 10000) {
    this.totalVirtualCapital = initialCapital;
  }

  addTrade(trade: VirtualTrade): void {
    this.trades.push(trade);
  }

  get totalTrades(): number {
    return this.trades.length;
  }

  get closedTrades(): VirtualTrade[] {
    return this.trades.filter((t) => t.status === 'closed');
  }

  get openTrades(): VirtualTrade[] {
    return this.trades.filter((t) => t.status === 'open');
  }

  get winningTrades(): VirtualTrade[] {
    return this.closedTrades.filter((t) => (t.actualProfitUsdt ?? 0) > 0);
  }

  get losingTrades(): VirtualTrade[] {
    return this.closedTrades.filter((t) => (t.actualProfitUsdt ?? 0) <= 0);
  }

  get totalProfitUsdt(): number {
    return this.closedTrades.reduce((sum, t) => sum + (t.actualProfitUsdt ?? 0), 0);
  }

  get totalExpectedProfitUsdt(): number {
    return this.trades.reduce((sum, t) => sum + t.expectedProfitUsdt, 0);
  }

  get winRate(): number {
    if (this.closedTrades.length === 0) return 0;
    return (this.winningTrades.length / this.closedTrades.length) * 100;
  }

  get averageProfitPercent(): number {
    if (this.closedTrades.length === 0) return 0;
    const sum = this.closedTrades.reduce((s, t) => s + t.expectedProfitPercent, 0);
    return sum / this.closedTrades.length;
  }

  get roi(): number {
    if (this.totalVirtualCapital === 0) return 0;
    return (this.totalProfitUsdt / this.totalVirtualCapital) * 100;
  }

  getStatsByStrategy(): Record<string, { count: number; profit: number }> {
    const stats: Record<string, { count: number; profit: number }> = {};
    for (const trade of this.closedTrades) {
      if (!stats[trade.strategy]) {
        stats[trade.strategy] = { count: 0, profit: 0 };
      }
      stats[trade.strategy].count++;
      stats[trade.strategy].profit += trade.actualProfitUsdt ?? 0;
    }
    return stats;
  }

  getRecentTrades(limit: number = 10): VirtualTrade[] {
    return [...this.trades].reverse().slice(0, limit);
  }
}
