import { ITradeRepository } from '../../domain/ports/ITradeRepository';
import { Portfolio } from '../../domain/entities/Portfolio';

export interface SessionStats {
  totalTrades: number;
  closedTrades: number;
  winningTrades: number;
  losingTrades: number;
  winRate: number;
  totalProfitUsdt: number;
  totalExpectedProfitUsdt: number;
  averageProfitPercent: number;
  roi: number;
  byStrategy: Record<string, { count: number; profit: number }>;
  bestTrade: { symbol: string; profit: number; strategy: string } | null;
  worstTrade: { symbol: string; profit: number; strategy: string } | null;
}

export class GenerateReportUseCase {
  constructor(
    private readonly repository: ITradeRepository,
    private readonly portfolio: Portfolio,
  ) {}

  async execute(): Promise<SessionStats> {
    const closed = this.portfolio.closedTrades;

    let bestTrade = null;
    let worstTrade = null;

    if (closed.length > 0) {
      const best = closed.reduce((a, b) =>
        (a.actualProfitUsdt ?? 0) > (b.actualProfitUsdt ?? 0) ? a : b,
      );
      const worst = closed.reduce((a, b) =>
        (a.actualProfitUsdt ?? 0) < (b.actualProfitUsdt ?? 0) ? a : b,
      );
      bestTrade = { symbol: best.symbol, profit: best.actualProfitUsdt ?? 0, strategy: best.strategy };
      worstTrade = { symbol: worst.symbol, profit: worst.actualProfitUsdt ?? 0, strategy: worst.strategy };
    }

    return {
      totalTrades: this.portfolio.totalTrades,
      closedTrades: this.portfolio.closedTrades.length,
      winningTrades: this.portfolio.winningTrades.length,
      losingTrades: this.portfolio.losingTrades.length,
      winRate: this.portfolio.winRate,
      totalProfitUsdt: this.portfolio.totalProfitUsdt,
      totalExpectedProfitUsdt: this.portfolio.totalExpectedProfitUsdt,
      averageProfitPercent: this.portfolio.averageProfitPercent,
      roi: this.portfolio.roi,
      byStrategy: this.portfolio.getStatsByStrategy(),
      bestTrade,
      worstTrade,
    };
  }
}
