import { OrderBook } from '../value-objects/OrderBook';
import { Fee } from '../value-objects/Fee';

export interface CrossExchangeProfitResult {
  isProfitable: boolean;
  profitUsdt: number;
  profitPercent: number;
  buyPrice: number;
  sellPrice: number;
  effectiveQty: number;
  totalCost: number;
  totalRevenue: number;
  buyFeeUsdt: number;
  sellFeeUsdt: number;
  netProfit: number;
}

export interface TriangularProfitResult {
  isProfitable: boolean;
  profitUsdt: number;
  profitPercent: number;
  path: string[];
  startAmount: number;
  endAmount: number;
  totalFees: number;
}

export class ProfitCalculator {
  calculateCrossExchangeProfit(
    buyBook: OrderBook,
    sellBook: OrderBook,
    buyFee: Fee,
    sellFee: Fee,
    positionSizeUsdt: number,
  ): CrossExchangeProfitResult {
    const buy = buyBook.fillBuyOrder(positionSizeUsdt);
    if (buy.filledQty === 0) {
      return this.emptyResult();
    }

    const sell = sellBook.fillSellOrder(buy.filledQty);
    if (sell.filledQty === 0) {
      return this.emptyResult();
    }

    const actualQty = Math.min(buy.filledQty, sell.filledQty);
    const totalCost = actualQty * buy.avgPrice;
    const totalRevenue = actualQty * sell.avgPrice;

    const buyFeeUsdt = buyFee.calculate(totalCost, 'taker');
    const sellFeeUsdt = sellFee.calculate(totalRevenue, 'taker');

    const netProfit = totalRevenue - totalCost - buyFeeUsdt - sellFeeUsdt;
    const profitPercent = (netProfit / totalCost) * 100;

    return {
      isProfitable: netProfit > 0,
      profitUsdt: netProfit,
      profitPercent,
      buyPrice: buy.avgPrice,
      sellPrice: sell.avgPrice,
      effectiveQty: actualQty,
      totalCost,
      totalRevenue,
      buyFeeUsdt,
      sellFeeUsdt,
      netProfit,
    };
  }

  calculateTriangularProfit(
    startAmount: number,
    rates: Array<{ from: string; to: string; rate: number; feePercent: number }>,
  ): TriangularProfitResult {
    const path = rates.map((r) => r.from);
    path.push(rates[rates.length - 1].to);

    let amount = startAmount;
    let totalFees = 0;

    for (const step of rates) {
      const fee = amount * (step.feePercent / 100);
      totalFees += fee;
      amount = (amount - fee) * step.rate;
    }

    const profitUsdt = amount - startAmount;
    const profitPercent = ((amount - startAmount) / startAmount) * 100;

    return {
      isProfitable: profitUsdt > 0,
      profitUsdt,
      profitPercent,
      path,
      startAmount,
      endAmount: amount,
      totalFees,
    };
  }

  calculateFuturesSpotProfit(
    spotPrice: number,
    futuresPrice: number,
    fundingRate: number,
    positionSizeUsdt: number,
    spotFee: Fee,
    futuresFee: Fee,
  ): { profitUsdt: number; profitPercent: number; basis: number; basisPercent: number } {
    const basis = futuresPrice - spotPrice;
    const basisPercent = (basis / spotPrice) * 100;

    const qty = positionSizeUsdt / spotPrice;
    const spotFeeUsdt = spotFee.calculate(positionSizeUsdt, 'taker');
    const futuresFeeUsdt = futuresFee.calculate(positionSizeUsdt, 'taker');

    const fundingIncome = positionSizeUsdt * Math.abs(fundingRate);
    const basisProfit = qty * Math.abs(basis);
    const totalFees = spotFeeUsdt + futuresFeeUsdt;

    const profitUsdt = basisProfit + fundingIncome - totalFees;
    const profitPercent = (profitUsdt / positionSizeUsdt) * 100;

    return { profitUsdt, profitPercent, basis, basisPercent };
  }

  private emptyResult(): CrossExchangeProfitResult {
    return {
      isProfitable: false,
      profitUsdt: 0,
      profitPercent: 0,
      buyPrice: 0,
      sellPrice: 0,
      effectiveQty: 0,
      totalCost: 0,
      totalRevenue: 0,
      buyFeeUsdt: 0,
      sellFeeUsdt: 0,
      netProfit: 0,
    };
  }
}
