import { ArbitrageOpportunity } from '../../domain/entities/ArbitrageOpportunity';
import { VirtualTrade } from '../../domain/entities/VirtualTrade';
import { Portfolio } from '../../domain/entities/Portfolio';
import { ITradeRepository } from '../../domain/ports/ITradeRepository';

export class ExecuteDemoTradeUseCase {
  constructor(
    private readonly repository: ITradeRepository,
    private readonly portfolio: Portfolio,
  ) {}

  async execute(opportunity: ArbitrageOpportunity): Promise<VirtualTrade> {
    const trade = new VirtualTrade(
      opportunity.strategy,
      opportunity.symbol,
      opportunity.positionSizeUsdt,
      opportunity.profitUsdt,
      opportunity.profitPercent,
      opportunity.details,
    );

    await this.repository.save(trade);
    this.portfolio.addTrade(trade);

    const slippage = 0.0002;
    const adjustedProfit = opportunity.profitUsdt * (1 - slippage);
    trade.close(adjustedProfit, 'Demo: closed with 0.02% slippage adjustment');
    await this.repository.save(trade);

    return trade;
  }
}
