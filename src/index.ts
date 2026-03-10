import { config } from './config/config';
import { ExchangeFactory } from './infrastructure/exchanges/ExchangeFactory';
import { FileTradeRepository } from './infrastructure/repositories/FileTradeRepository';
import { Portfolio } from './domain/entities/Portfolio';
import { ScanOpportunitiesUseCase } from './application/use-cases/ScanOpportunitiesUseCase';
import { ExecuteDemoTradeUseCase } from './application/use-cases/ExecuteDemoTradeUseCase';
import { GenerateReportUseCase } from './application/use-cases/GenerateReportUseCase';
import { ArbitrageBotService } from './application/services/ArbitrageBotService';
import { Dashboard } from './presentation/Dashboard';
import { TriangularPathConfig } from './application/use-cases/ScanOpportunitiesUseCase';
import { TelegramAlertService } from './infrastructure/notifications/TelegramAlertService';

const dashboard = new Dashboard();

const TRIANGULAR_PATHS: TriangularPathConfig[] = [
  { exchange: 'binance', pairs: ['ETH/BTC', 'ETH/USDT', 'BTC/USDT'], coins: ['USDT', 'BTC', 'ETH', 'USDT'] },
  { exchange: 'binance', pairs: ['BNB/BTC', 'BNB/USDT', 'BTC/USDT'], coins: ['USDT', 'BTC', 'BNB', 'USDT'] },
  { exchange: 'binance', pairs: ['SOL/BTC', 'SOL/USDT', 'BTC/USDT'], coins: ['USDT', 'BTC', 'SOL', 'USDT'] },
  { exchange: 'binance', pairs: ['LTC/BTC', 'LTC/USDT', 'BTC/USDT'], coins: ['USDT', 'BTC', 'LTC', 'USDT'] },
  { exchange: 'binance', pairs: ['XRP/BTC', 'XRP/USDT', 'BTC/USDT'], coins: ['USDT', 'BTC', 'XRP', 'USDT'] },
  { exchange: 'binance', pairs: ['ADA/BTC', 'ADA/USDT', 'BTC/USDT'], coins: ['USDT', 'BTC', 'ADA', 'USDT'] },
  { exchange: 'bybit', pairs: ['ETH/BTC', 'ETH/USDT', 'BTC/USDT'], coins: ['USDT', 'BTC', 'ETH', 'USDT'] },
  { exchange: 'bybit', pairs: ['SOL/BTC', 'SOL/USDT', 'BTC/USDT'], coins: ['USDT', 'BTC', 'SOL', 'USDT'] },
  { exchange: 'kucoin', pairs: ['ETH/BTC', 'ETH/USDT', 'BTC/USDT'], coins: ['USDT', 'BTC', 'ETH', 'USDT'] },
  { exchange: 'kucoin', pairs: ['SOL/BTC', 'SOL/USDT', 'BTC/USDT'], coins: ['USDT', 'BTC', 'SOL', 'USDT'] },
];

function hasCreds(apiKey: string, secret: string): boolean {
  return !!(apiKey && secret);
}

async function bootstrap(): Promise<void> {
  dashboard.printHeader(config.mode);
  dashboard.printInfo(`Режим: ${config.mode.toUpperCase()}`);
  dashboard.printInfo(`Интервал сканирования: ${config.scanIntervalMs}мс`);
  dashboard.printInfo(`Мин. прибыль: ${config.minProfitPercent}%`);
  dashboard.printInfo(`Макс. позиция: $${config.maxPositionUsdt}`);

  const factory = new ExchangeFactory();
  const cx = config.exchanges;

  const hasLiveCreds = {
    binance: hasCreds(cx.binance.apiKey, cx.binance.secret),
    bybit: hasCreds(cx.bybit.apiKey, cx.bybit.secret),
    okx: hasCreds(cx.okx.apiKey, cx.okx.secret),
    kucoin: hasCreds(cx.kucoin.apiKey, cx.kucoin.secret),
    gateio: hasCreds(cx.gateio.apiKey, cx.gateio.secret),
    mexc: hasCreds(cx.mexc.apiKey, cx.mexc.secret),
    bitget: hasCreds(cx.bitget.apiKey, cx.bitget.secret),
    htx: hasCreds(cx.htx.apiKey, cx.htx.secret),
  };

  const exchangeNames = Object.entries(hasLiveCreds)
    .map(([name, live]) => `${name}${live ? '(api)' : '(pub)'}`)
    .join(' | ');
  dashboard.printInfo(`Биржи: ${exchangeNames}`);

  const spotExchanges = [
    factory.createBinanceSpot(hasLiveCreds.binance ? cx.binance : undefined),
    factory.createBybit(hasLiveCreds.bybit ? cx.bybit : undefined),
    factory.createOkx(hasLiveCreds.okx ? cx.okx : undefined),
    factory.createKucoin(hasLiveCreds.kucoin ? cx.kucoin : undefined),
    factory.createGateio(hasLiveCreds.gateio ? cx.gateio : undefined),
    factory.createMexc(hasLiveCreds.mexc ? cx.mexc : undefined),
    factory.createBitget(hasLiveCreds.bitget ? cx.bitget : undefined),
    factory.createHtx(hasLiveCreds.htx ? cx.htx : undefined),
  ];

  const futuresExchanges = [
    factory.createBinanceFutures(hasLiveCreds.binance ? cx.binance : undefined),
    factory.createBybitFutures(hasLiveCreds.bybit ? cx.bybit : undefined),
  ];

  dashboard.printInfo('Проверка доступности бирж...');
  const availability = await Promise.all(spotExchanges.map((ex) => ex.isAvailable().catch(() => false)));
  spotExchanges.forEach((ex, i) => {
    if (availability[i]) {
      dashboard.printSuccess(`${ex.info.id} доступен`);
    } else {
      dashboard.printError(`${ex.info.id} недоступен, пропускаем`);
    }
  });

  const activeExchanges = spotExchanges.filter((_, i) => availability[i]);

  if (activeExchanges.length === 0) {
    dashboard.printError('Ни одна биржа не доступна. Проверьте интернет-соединение.');
    process.exit(1);
  }

  const repository = new FileTradeRepository(config.logFile);
  const portfolio = new Portfolio(10000);

  const scanConfig = {
    symbols: config.pairs,
    positionSizeUsdt: config.maxPositionUsdt,
    minProfitPercent: config.minProfitPercent,
    triangularPaths: TRIANGULAR_PATHS,
    enableCrossExchange: config.strategies.crossExchange,
    enableTriangular: config.strategies.triangular,
    enableFuturesSpot: config.strategies.futuresSpot,
  };

  const scanner = new ScanOpportunitiesUseCase(activeExchanges, futuresExchanges);
  const executor = new ExecuteDemoTradeUseCase(repository, portfolio);
  const reporter = new GenerateReportUseCase(repository, portfolio);

  const alertService =
    config.telegram.botToken && config.telegram.chatId
      ? new TelegramAlertService(config.telegram.botToken, config.telegram.chatId)
      : undefined;

  if (alertService) {
    dashboard.printSuccess('Telegram-алерты подключены');
  } else {
    dashboard.printInfo('Telegram-алерты отключены (TELEGRAM_BOT_TOKEN не задан)');
  }

  const bot = new ArbitrageBotService(
    scanner,
    executor,
    reporter,
    scanConfig,
    config.mode,
    config.scanIntervalMs,
    alertService,
  );

  bot.setScanHandler((opps, duration) => {
    dashboard.printBotStats(bot.getStats());
    dashboard.printScanResult(opps, duration);
  });

  bot.setOpportunityHandler((opp, trade) => {
    dashboard.printOpportunity(opp, trade);
  });

  bot.setErrorHandler((err) => {
    dashboard.printError(err);
  });

  process.on('SIGINT', async () => {
    console.log('\n');
    bot.stop();
    dashboard.printInfo('Остановка бота...');
    const report = await reporter.execute();
    dashboard.printReport(report);
    process.exit(0);
  });

  process.on('SIGTERM', async () => {
    bot.stop();
    const report = await reporter.execute();
    dashboard.printReport(report);
    process.exit(0);
  });

  if (config.mode === 'report') {
    const report = await reporter.execute();
    dashboard.printReport(report);
    return;
  }

  dashboard.printSuccess('Бот запущен. Нажмите Ctrl+C для остановки и просмотра отчёта.');
  console.log();

  await bot.start();
}

bootstrap().catch((err) => {
  console.error('Fatal error:', err);
  process.exit(1);
});
