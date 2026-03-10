import { ArbitrageOpportunity } from '../entities/ArbitrageOpportunity';
import { OrderBook } from '../value-objects/OrderBook';
import { Fee } from '../value-objects/Fee';
import { Ticker, FuturesTicker } from '../ports/IExchange';
import { ProfitCalculator } from './ProfitCalculator';

export interface ExchangeOrderBooks {
  exchangeId: string;
  fee: Fee;
  books: Map<string, OrderBook>;
  tickers: Map<string, Ticker>;
}

export interface TriangularPath {
  exchange: string;
  pairs: string[];
  coins: string[];
}

export class ArbitrageDetector {
  private readonly calculator = new ProfitCalculator();

  detectCrossExchange(
    exchanges: ExchangeOrderBooks[],
    symbol: string,
    positionSizeUsdt: number,
    minProfitPercent: number,
  ): ArbitrageOpportunity[] {
    const opportunities: ArbitrageOpportunity[] = [];

    for (let i = 0; i < exchanges.length; i++) {
      for (let j = 0; j < exchanges.length; j++) {
        if (i === j) continue;

        const buyExchange = exchanges[i];
        const sellExchange = exchanges[j];

        const buyBook = buyExchange.books.get(symbol);
        const sellBook = sellExchange.books.get(symbol);

        if (!buyBook || !sellBook) continue;
        if (buyBook.bestAsk === 0 || sellBook.bestBid === 0) continue;
        if (buyBook.bestAsk >= sellBook.bestBid) continue;

        const result = this.calculator.calculateCrossExchangeProfit(
          buyBook,
          sellBook,
          buyExchange.fee,
          sellExchange.fee,
          positionSizeUsdt,
        );

        if (!result.isProfitable || result.profitPercent < minProfitPercent) continue;

        opportunities.push(
          new ArbitrageOpportunity(
            'cross_exchange',
            symbol,
            result.profitUsdt,
            result.profitPercent,
            positionSizeUsdt,
            {
              buyExchange: buyExchange.exchangeId,
              sellExchange: sellExchange.exchangeId,
              buyPrice: result.buyPrice,
              sellPrice: result.sellPrice,
              buyFee: result.buyFeeUsdt,
              sellFee: result.sellFeeUsdt,
              maxQty: result.effectiveQty,
              symbol,
            },
          ),
        );
      }
    }

    return opportunities;
  }

  detectTriangular(
    exchangeId: string,
    fee: Fee,
    tickers: Map<string, Ticker>,
    triangularPaths: TriangularPath[],
    startAmountUsdt: number,
    minProfitPercent: number,
  ): ArbitrageOpportunity[] {
    const opportunities: ArbitrageOpportunity[] = [];

    for (const path of triangularPaths) {
      if (path.exchange !== exchangeId) continue;

      const rates: Array<{ from: string; to: string; rate: number; feePercent: number }> = [];
      let valid = true;

      for (let i = 0; i < path.pairs.length; i++) {
        const pair = path.pairs[i];
        const ticker = tickers.get(pair);

        if (!ticker || ticker.ask === 0) {
          valid = false;
          break;
        }

        const fromCoin = path.coins[i];
        const toCoin = path.coins[i + 1];
        const baseOfPair = pair.split('/')[0];

        const rate = fromCoin === baseOfPair ? ticker.bid : 1 / ticker.ask;

        rates.push({
          from: fromCoin,
          to: toCoin,
          rate,
          feePercent: fee.getTakerPercent(),
        });
      }

      if (!valid) continue;

      const result = this.calculator.calculateTriangularProfit(startAmountUsdt, rates);

      if (!result.isProfitable || result.profitPercent < minProfitPercent) continue;

      opportunities.push(
        new ArbitrageOpportunity(
          'triangular',
          path.coins.join('→'),
          result.profitUsdt,
          result.profitPercent,
          startAmountUsdt,
          {
            exchange: exchangeId,
            path: result.path,
            startAmount: result.startAmount,
            endAmount: result.endAmount,
            fees: result.totalFees,
          },
        ),
      );
    }

    return opportunities;
  }

  detectFuturesSpot(
    exchangeId: string,
    symbol: string,
    spotTicker: Ticker,
    futuresTicker: FuturesTicker,
    fee: Fee,
    positionSizeUsdt: number,
    minProfitPercent: number,
  ): ArbitrageOpportunity | null {
    const result = this.calculator.calculateFuturesSpotProfit(
      spotTicker.last,
      futuresTicker.last,
      futuresTicker.fundingRate,
      positionSizeUsdt,
      fee,
      fee,
    );

    if (result.profitPercent < minProfitPercent) return null;

    return new ArbitrageOpportunity(
      'futures_spot',
      symbol,
      result.profitUsdt,
      result.profitPercent,
      positionSizeUsdt,
      {
        exchange: exchangeId,
        symbol,
        spotPrice: spotTicker.last,
        futuresPrice: futuresTicker.last,
        fundingRate: futuresTicker.fundingRate,
        basis: result.basis,
        basisPercent: result.basisPercent,
      },
    );
  }
}
