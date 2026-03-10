import chalk from 'chalk';
import Table from 'cli-table3';
import { ArbitrageOpportunity } from '../domain/entities/ArbitrageOpportunity';
import { VirtualTrade } from '../domain/entities/VirtualTrade';
import { SessionStats } from '../application/use-cases/GenerateReportUseCase';
import { BotStats } from '../application/services/ArbitrageBotService';
import { BotMode } from '../config/config';
import {
  CrossExchangeDetails,
  TriangularDetails,
  FuturesSpotDetails,
} from '../domain/entities/ArbitrageOpportunity';

export class Dashboard {
  private readonly startTime = new Date();

  printHeader(mode: BotMode): void {
    console.clear();
    const modeLabel =
      mode === 'demo'
        ? chalk.yellow('● DEMO MODE (виртуальные сделки)')
        : chalk.red('● LIVE MODE (реальные сделки)');

    console.log(chalk.cyan('═'.repeat(70)));
    console.log(chalk.cyan.bold('   CRYPTO ARBITRAGE BOT') + '   ' + modeLabel);
    console.log(chalk.cyan('═'.repeat(70)));
    console.log(chalk.gray(`  Запущен: ${this.startTime.toLocaleString()}`));
  }

  printBotStats(stats: BotStats): void {
    const uptime = Math.floor((Date.now() - this.startTime.getTime()) / 1000);
    const m = Math.floor(uptime / 60);
    const s = uptime % 60;

    console.log();
    console.log(chalk.bold('─── Статус бота ───────────────────────────────────────────────────'));
    console.log(
      `  Статус: ${stats.isRunning ? chalk.green('Работает') : chalk.red('Остановлен')}` +
        `  |  Время работы: ${chalk.white(`${m}м ${s}с`)}` +
        `  |  Сканирований: ${chalk.white(stats.scanCount)}`,
    );
    console.log(
      `  Найдено возможностей: ${chalk.yellow(stats.totalOpportunitiesFound)}` +
        `  |  Сделок: ${chalk.green(stats.totalTradesExecuted)}` +
        `  |  Посл. скан: ${stats.lastScanDurationMs}мс`,
    );
    if (stats.errors.length > 0) {
      console.log(chalk.red(`  Последняя ошибка: ${stats.errors[stats.errors.length - 1]?.slice(0, 80)}`));
    }
  }

  printOpportunity(opp: ArbitrageOpportunity, trade: VirtualTrade): void {
    const icon = opp.strategy === 'cross_exchange' ? '⇄' : opp.strategy === 'triangular' ? '△' : '◈';
    const strategyLabel = {
      cross_exchange: 'МЕЖБИРЖ',
      triangular: 'ТРЕУГОЛ',
      futures_spot: 'ФЬЮ-СПОТ',
    }[opp.strategy];

    const profitColor = opp.profitPercent >= 0.5 ? chalk.green : chalk.yellow;

    console.log();
    console.log(
      chalk.bold.white(`${icon} [${strategyLabel}] `) +
        chalk.white(opp.symbol) +
        '  ' +
        profitColor(`+${opp.profitPercent.toFixed(4)}%`) +
        '  ' +
        profitColor(`+$${opp.profitUsdt.toFixed(4)}`),
    );

    if (opp.strategy === 'cross_exchange') {
      const d = opp.details as CrossExchangeDetails;
      console.log(
        chalk.gray(
          `  Купить на ${d.buyExchange} по $${d.buyPrice.toFixed(2)} → ` +
            `Продать на ${d.sellExchange} по $${d.sellPrice.toFixed(2)} ` +
            `| Объём: ${d.maxQty.toFixed(6)} ${d.symbol.split('/')[0]}`,
        ),
      );
    } else if (opp.strategy === 'triangular') {
      const d = opp.details as TriangularDetails;
      console.log(chalk.gray(`  Путь: ${d.path.join(' → ')} | ${d.startAmount.toFixed(2)} → ${d.endAmount.toFixed(2)} USDT`));
    } else {
      const d = opp.details as FuturesSpotDetails;
      console.log(
        chalk.gray(
          `  Спот: $${d.spotPrice.toFixed(2)} | Фьюч: $${d.futuresPrice.toFixed(2)} ` +
            `| Базис: ${d.basisPercent.toFixed(4)}% | Ставка: ${(d.fundingRate * 100).toFixed(4)}%`,
        ),
      );
    }

    console.log(
      chalk.gray(
        `  Позиция: $${opp.positionSizeUsdt} | ` +
          chalk.green(`Виртуальная прибыль: +$${(trade.actualProfitUsdt ?? 0).toFixed(4)}`),
      ),
    );
  }

  printScanResult(opportunities: ArbitrageOpportunity[], durationMs: number): void {
    const now = new Date().toLocaleTimeString();
    if (opportunities.length === 0) {
      process.stdout.write(
        chalk.gray(`\r  [${now}] Скан завершён за ${durationMs}мс | Возможностей не найдено   `),
      );
    } else {
      console.log();
      console.log(
        chalk.bold(`\n  [${now}] Найдено ${chalk.yellow(opportunities.length)} возможностей за ${durationMs}мс`),
      );
    }
  }

  printReport(stats: SessionStats): void {
    console.log();
    console.log(chalk.cyan('═'.repeat(70)));
    console.log(chalk.cyan.bold('   ОТЧЁТ СЕССИИ'));
    console.log(chalk.cyan('═'.repeat(70)));

    const table = new Table({
      head: [chalk.white('Метрика'), chalk.white('Значение')],
      colWidths: [35, 30],
      style: { head: [], border: ['gray'] },
    });

    table.push(
      ['Всего сделок', chalk.white(stats.totalTrades)],
      ['Закрытых сделок', chalk.white(stats.closedTrades)],
      ['Прибыльных', chalk.green(stats.winningTrades)],
      ['Убыточных', chalk.red(stats.losingTrades)],
      ['Винрейт', chalk.yellow(`${stats.winRate.toFixed(2)}%`)],
      ['Ожидаемая прибыль', chalk.yellow(`$${stats.totalExpectedProfitUsdt.toFixed(4)}`)],
      ['Фактическая прибыль (demo)', chalk.green(`$${stats.totalProfitUsdt.toFixed(4)}`)],
      ['Средний % прибыли', chalk.white(`${stats.averageProfitPercent.toFixed(4)}%`)],
      ['ROI (от 10k USDT)', chalk.white(`${stats.roi.toFixed(4)}%`)],
    );

    console.log(table.toString());

    if (Object.keys(stats.byStrategy).length > 0) {
      console.log(chalk.bold('\n  По стратегиям:'));
      const stTable = new Table({
        head: [chalk.white('Стратегия'), chalk.white('Сделок'), chalk.white('Прибыль')],
        style: { head: [], border: ['gray'] },
      });
      for (const [strategy, data] of Object.entries(stats.byStrategy)) {
        stTable.push([
          strategy,
          String(data.count),
          chalk.green(`$${data.profit.toFixed(4)}`),
        ]);
      }
      console.log(stTable.toString());
    }

    if (stats.bestTrade) {
      console.log(chalk.green(`\n  Лучшая сделка: ${stats.bestTrade.symbol} +$${stats.bestTrade.profit.toFixed(4)} [${stats.bestTrade.strategy}]`));
    }
  }

  printError(err: string): void {
    console.log(chalk.red(`  [ERR] ${err.slice(0, 100)}`));
  }

  printInfo(msg: string): void {
    console.log(chalk.gray(`  ${msg}`));
  }

  printSuccess(msg: string): void {
    console.log(chalk.green(`  ✓ ${msg}`));
  }
}
