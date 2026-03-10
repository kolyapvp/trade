export interface OrderBookLevel {
  price: number;
  quantity: number;
}

export class OrderBook {
  constructor(
    public readonly symbol: string,
    public readonly exchangeId: string,
    public readonly bids: OrderBookLevel[],
    public readonly asks: OrderBookLevel[],
    public readonly timestamp: number,
  ) {}

  get bestBid(): number {
    return this.bids[0]?.price ?? 0;
  }

  get bestAsk(): number {
    return this.asks[0]?.price ?? 0;
  }

  get spread(): number {
    if (this.bestAsk === 0) return 0;
    return ((this.bestAsk - this.bestBid) / this.bestAsk) * 100;
  }

  fillBuyOrder(usdtAmount: number): { filledQty: number; avgPrice: number; totalCost: number } {
    let remaining = usdtAmount;
    let filledQty = 0;
    let totalCost = 0;

    for (const level of this.asks) {
      if (remaining <= 0) break;
      const levelCost = level.price * level.quantity;
      if (levelCost <= remaining) {
        filledQty += level.quantity;
        totalCost += levelCost;
        remaining -= levelCost;
      } else {
        const qty = remaining / level.price;
        filledQty += qty;
        totalCost += remaining;
        remaining = 0;
      }
    }

    const avgPrice = filledQty > 0 ? totalCost / filledQty : 0;
    return { filledQty, avgPrice, totalCost };
  }

  fillSellOrder(qty: number): { filledQty: number; avgPrice: number; totalRevenue: number } {
    let remaining = qty;
    let filledQty = 0;
    let totalRevenue = 0;

    for (const level of this.bids) {
      if (remaining <= 0) break;
      if (level.quantity <= remaining) {
        filledQty += level.quantity;
        totalRevenue += level.price * level.quantity;
        remaining -= level.quantity;
      } else {
        filledQty += remaining;
        totalRevenue += level.price * remaining;
        remaining = 0;
      }
    }

    const avgPrice = filledQty > 0 ? totalRevenue / filledQty : 0;
    return { filledQty, avgPrice, totalRevenue };
  }

  liquidityAtBid(depth: number = 5): number {
    return this.bids.slice(0, depth).reduce((s, l) => s + l.price * l.quantity, 0);
  }

  liquidityAtAsk(depth: number = 5): number {
    return this.asks.slice(0, depth).reduce((s, l) => s + l.price * l.quantity, 0);
  }
}
