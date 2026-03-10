export type FeeType = 'maker' | 'taker';

export class Fee {
  constructor(
    public readonly maker: number,
    public readonly taker: number,
  ) {}

  getTakerPercent(): number {
    return this.taker * 100;
  }

  getMakerPercent(): number {
    return this.maker * 100;
  }

  calculate(amount: number, type: FeeType = 'taker'): number {
    return amount * (type === 'taker' ? this.taker : this.maker);
  }

  static default(): Fee {
    return new Fee(0.001, 0.001);
  }

  static binance(): Fee {
    return new Fee(0.001, 0.001);
  }

  static bybit(): Fee {
    return new Fee(0.001, 0.001);
  }

  static okx(): Fee {
    return new Fee(0.0008, 0.001);
  }

  static kucoin(): Fee {
    return new Fee(0.001, 0.001);
  }

  static gateio(): Fee {
    return new Fee(0.002, 0.002);
  }

  static mexc(): Fee {
    return new Fee(0.0, 0.002);
  }

  static bitget(): Fee {
    return new Fee(0.001, 0.001);
  }

  static htx(): Fee {
    return new Fee(0.002, 0.002);
  }
}
