import { ScanOpportunitiesUseCase, ScanConfig } from '../use-cases/ScanOpportunitiesUseCase';
import { ExecuteDemoTradeUseCase } from '../use-cases/ExecuteDemoTradeUseCase';
import { GenerateReportUseCase } from '../use-cases/GenerateReportUseCase';
import { ArbitrageOpportunity } from '../../domain/entities/ArbitrageOpportunity';
import {
  CrossExchangeDetails,
  TriangularDetails,
  FuturesSpotDetails,
} from '../../domain/entities/ArbitrageOpportunity';
import { VirtualTrade } from '../../domain/entities/VirtualTrade';
import { BotMode } from '../../config/config';
import { IAlertService } from '../../domain/ports/IAlertService';

export interface BotStats {
  isRunning: boolean;
  scanCount: number;
  lastScanAt: Date | null;
  lastScanDurationMs: number;
  totalOpportunitiesFound: number;
  totalTradesExecuted: number;
  errors: string[];
}

export type OpportunityHandler = (opp: ArbitrageOpportunity, trade: VirtualTrade) => void;
export type ScanHandler = (opps: ArbitrageOpportunity[], durationMs: number) => void;
export type ErrorHandler = (err: string) => void;

export class ArbitrageBotService {
  private running = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stats: BotStats = {
    isRunning: false,
    scanCount: 0,
    lastScanAt: null,
    lastScanDurationMs: 0,
    totalOpportunitiesFound: 0,
    totalTradesExecuted: 0,
    errors: [],
  };

  private onOpportunity: OpportunityHandler | null = null;
  private onScan: ScanHandler | null = null;
  private onError: ErrorHandler | null = null;

  constructor(
    private readonly scanner: ScanOpportunitiesUseCase,
    private readonly executor: ExecuteDemoTradeUseCase,
    private readonly reporter: GenerateReportUseCase,
    private readonly scanConfig: ScanConfig,
    private readonly mode: BotMode,
    private readonly scanIntervalMs: number,
    private readonly alertService?: IAlertService,
  ) {}

  setOpportunityHandler(handler: OpportunityHandler): void {
    this.onOpportunity = handler;
  }

  setScanHandler(handler: ScanHandler): void {
    this.onScan = handler;
  }

  setErrorHandler(handler: ErrorHandler): void {
    this.onError = handler;
  }

  async start(): Promise<void> {
    this.running = true;
    this.stats.isRunning = true;
    await this.runCycle();
  }

  stop(): void {
    this.running = false;
    this.stats.isRunning = false;
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  getStats(): BotStats {
    return { ...this.stats };
  }

  async getReport() {
    return this.reporter.execute();
  }

  private buildAlertDetails(opp: ArbitrageOpportunity): string {
    if (opp.strategy === 'cross_exchange') {
      const d = opp.details as CrossExchangeDetails;
      return `Купить на ${d.buyExchange} по $${d.buyPrice.toFixed(2)} → продать на ${d.sellExchange} по $${d.sellPrice.toFixed(2)} | Объём: ${d.maxQty.toFixed(6)}`;
    }
    if (opp.strategy === 'triangular') {
      const d = opp.details as TriangularDetails;
      return `Путь: ${d.path.join(' → ')} | ${d.startAmount.toFixed(2)} → ${d.endAmount.toFixed(2)} USDT`;
    }
    const d = opp.details as FuturesSpotDetails;
    return `Спот: $${d.spotPrice.toFixed(2)} | Фьюч: $${d.futuresPrice.toFixed(2)} | Базис: ${d.basisPercent.toFixed(4)}% | Ставка: ${(d.fundingRate * 100).toFixed(4)}%`;
  }

  private async runCycle(): Promise<void> {
    if (!this.running) return;

    try {
      const result = await this.scanner.execute(this.scanConfig);

      this.stats.scanCount++;
      this.stats.lastScanAt = result.scannedAt;
      this.stats.lastScanDurationMs = result.durationMs;
      this.stats.totalOpportunitiesFound += result.opportunities.length;

      if (result.errors.length > 0) {
        this.stats.errors = result.errors.slice(-10);
        result.errors.forEach((e) => this.onError?.(e));
      }

      this.onScan?.(result.opportunities, result.durationMs);

      for (const opp of result.opportunities) {
        if (!opp.isProfitable(this.scanConfig.minProfitPercent)) continue;

        if (this.mode === 'demo') {
          const trade = await this.executor.execute(opp);
          this.stats.totalTradesExecuted++;
          this.onOpportunity?.(opp, trade);

          if (this.alertService) {
            this.alertService
              .sendTradeAlert({
                strategy: opp.strategy,
                symbol: opp.symbol,
                profitPercent: opp.profitPercent,
                profitUsdt: trade.actualProfitUsdt ?? opp.profitUsdt,
                positionUsdt: opp.positionSizeUsdt,
                details: this.buildAlertDetails(opp),
                timestamp: new Date(),
              })
              .catch(() => {});
          }
        }
      }
    } catch (err) {
      const msg = `Bot cycle error: ${String(err)}`;
      this.stats.errors.push(msg);
      this.onError?.(msg);
    }

    if (this.running) {
      this.timer = setTimeout(() => this.runCycle(), this.scanIntervalMs);
    }
  }
}
