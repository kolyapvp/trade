import { IExchange } from '../../domain/ports/IExchange';
import { ArbitrageDetector, ExchangeOrderBooks } from '../../domain/services/ArbitrageDetector';
import { ArbitrageOpportunity } from '../../domain/entities/ArbitrageOpportunity';
import { OrderBook } from '../../domain/value-objects/OrderBook';
import { Ticker } from '../../domain/ports/IExchange';

export interface TriangularPathConfig {
  exchange: string;
  pairs: string[];
  coins: string[];
}

export interface ScanConfig {
  symbols: string[];
  positionSizeUsdt: number;
  minProfitPercent: number;
  triangularPaths: TriangularPathConfig[];
  enableCrossExchange: boolean;
  enableTriangular: boolean;
  enableFuturesSpot: boolean;
}

export interface ScanResult {
  opportunities: ArbitrageOpportunity[];
  scannedAt: Date;
  durationMs: number;
  errors: string[];
}

export class ScanOpportunitiesUseCase {
  private readonly detector = new ArbitrageDetector();

  constructor(
    private readonly spotExchanges: IExchange[],
    private readonly futuresExchanges: IExchange[],
  ) {}

  async execute(config: ScanConfig): Promise<ScanResult> {
    const start = Date.now();
    const opportunities: ArbitrageOpportunity[] = [];
    const errors: string[] = [];

    const exchangeData: ExchangeOrderBooks[] = [];

    await Promise.allSettled(
      this.spotExchanges.map(async (exchange) => {
        const books = new Map<string, OrderBook>();
        const tickers = new Map<string, Ticker>();

        await Promise.allSettled(
          config.symbols.map(async (symbol) => {
            try {
              const [book, ticker] = await Promise.all([
                exchange.fetchOrderBook(symbol, 20),
                exchange.fetchTicker(symbol),
              ]);
              books.set(symbol, book);
              tickers.set(symbol, ticker);
            } catch (err) {
              errors.push(`${exchange.info.id} ${symbol}: ${String(err)}`);
            }
          }),
        );

        if (books.size > 0) {
          exchangeData.push({
            exchangeId: exchange.info.id,
            fee: exchange.info.fee,
            books,
            tickers,
          });
        }
      }),
    );

    if (config.enableCrossExchange && exchangeData.length >= 2) {
      for (const symbol of config.symbols) {
        const found = this.detector.detectCrossExchange(
          exchangeData,
          symbol,
          config.positionSizeUsdt,
          config.minProfitPercent,
        );
        opportunities.push(...found);
      }
    }

    if (config.enableTriangular) {
      for (const exData of exchangeData) {
        const found = this.detector.detectTriangular(
          exData.exchangeId,
          exData.fee,
          exData.tickers,
          config.triangularPaths,
          config.positionSizeUsdt,
          config.minProfitPercent,
        );
        opportunities.push(...found);
      }
    }

    if (config.enableFuturesSpot && this.futuresExchanges.length > 0) {
      for (const spotEx of this.spotExchanges) {
        const spotData = exchangeData.find((d) => d.exchangeId === spotEx.info.id);
        if (!spotData) continue;

        const futuresEx = this.futuresExchanges.find((f) =>
          f.info.id.includes(spotEx.info.id.replace('spot', '').replace('_spot', '')),
        );
        if (!futuresEx) continue;

        for (const symbol of config.symbols) {
          try {
            const spotTicker = spotData.tickers.get(symbol);
            const futuresTicker = await futuresEx.fetchFuturesTicker(symbol);

            if (spotTicker && futuresTicker) {
              const opp = this.detector.detectFuturesSpot(
                spotEx.info.id,
                symbol,
                spotTicker,
                futuresTicker,
                spotEx.info.fee,
                config.positionSizeUsdt,
                config.minProfitPercent,
              );
              if (opp) opportunities.push(opp);
            }
          } catch (err) {
            errors.push(`futures-spot ${symbol}: ${String(err)}`);
          }
        }
      }
    }

    opportunities.sort((a, b) => b.profitPercent - a.profitPercent);

    return {
      opportunities,
      scannedAt: new Date(),
      durationMs: Date.now() - start,
      errors,
    };
  }
}
