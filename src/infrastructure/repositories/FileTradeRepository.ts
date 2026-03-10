import * as fs from 'fs';
import * as path from 'path';
import { ITradeRepository } from '../../domain/ports/ITradeRepository';
import { VirtualTrade } from '../../domain/entities/VirtualTrade';

export class FileTradeRepository implements ITradeRepository {
  private readonly filePath: string;
  private trades: VirtualTrade[] = [];

  constructor(fileName: string = 'trades.json') {
    this.filePath = path.resolve(process.cwd(), fileName);
    this.load();
  }

  async save(trade: VirtualTrade): Promise<void> {
    const existing = this.trades.findIndex((t) => t.id === trade.id);
    if (existing >= 0) {
      this.trades[existing] = trade;
    } else {
      this.trades.push(trade);
    }
    await this.persist();
  }

  async findAll(): Promise<VirtualTrade[]> {
    return [...this.trades];
  }

  async findById(id: string): Promise<VirtualTrade | undefined> {
    return this.trades.find((t) => t.id === id);
  }

  async findByStrategy(strategy: string): Promise<VirtualTrade[]> {
    return this.trades.filter((t) => t.strategy === strategy);
  }

  async clear(): Promise<void> {
    this.trades = [];
    await this.persist();
  }

  private load(): void {
    if (!fs.existsSync(this.filePath)) return;
    try {
      const raw = fs.readFileSync(this.filePath, 'utf-8');
      const data = JSON.parse(raw) as ReturnType<VirtualTrade['toJSON']>[];
      this.trades = data.map((d) => this.deserialize(d));
    } catch {
      this.trades = [];
    }
  }

  private async persist(): Promise<void> {
    const data = this.trades.map((t) => t.toJSON());
    fs.writeFileSync(this.filePath, JSON.stringify(data, null, 2), 'utf-8');
  }

  private deserialize(data: ReturnType<VirtualTrade['toJSON']>): VirtualTrade {
    const trade = new VirtualTrade(
      data.strategy as VirtualTrade['strategy'],
      data.symbol as string,
      data.positionSizeUsdt as number,
      data.expectedProfitUsdt as number,
      data.expectedProfitPercent as number,
      data.details as VirtualTrade['details'],
    );

    if (data.status === 'closed' && data.actualProfitUsdt !== undefined) {
      trade.close(data.actualProfitUsdt as number, data.notes as string | undefined);
    } else if (data.status === 'failed') {
      trade.fail(data.notes as string ?? 'unknown');
    }

    return trade;
  }
}
