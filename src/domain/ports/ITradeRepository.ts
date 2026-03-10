import { VirtualTrade } from '../entities/VirtualTrade';

export interface ITradeRepository {
  save(trade: VirtualTrade): Promise<void>;
  findAll(): Promise<VirtualTrade[]>;
  findById(id: string): Promise<VirtualTrade | undefined>;
  findByStrategy(strategy: string): Promise<VirtualTrade[]>;
  clear(): Promise<void>;
}
