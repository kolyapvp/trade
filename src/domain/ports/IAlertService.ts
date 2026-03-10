export interface TradeAlert {
  strategy: string;
  symbol: string;
  profitPercent: number;
  profitUsdt: number;
  positionUsdt: number;
  details: string;
  timestamp: Date;
}

export interface IAlertService {
  sendTradeAlert(alert: TradeAlert): Promise<void>;
}
