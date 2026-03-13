from __future__ import annotations

from dataclasses import asdict

from prometheus_client import Counter, Gauge, Histogram, start_http_server

from ..domain.ports import IMetricsService, ScanTelemetry, SignalTelemetry, TradeTelemetry


class NullMetricsService(IMetricsService):
    def start(self) -> None:
        return

    def set_bot_running(self, is_running: bool) -> None:
        return

    def set_open_positions(self, total: int) -> None:
        return

    def record_scan(self, telemetry: ScanTelemetry) -> None:
        return

    def set_exchange_balance(self, exchange: str, total_balance_usdt: float) -> None:
        return

    def set_total_balance(self, total_balance_usdt: float) -> None:
        return

    def record_signal(self, telemetry: SignalTelemetry) -> None:
        return

    def record_trade(self, telemetry: TradeTelemetry) -> None:
        return

    def record_error(self, stage: str, exchange: str = '', symbol: str = '') -> None:
        return


class PrometheusMetricsService(IMetricsService):
    _signal_labels = (
        'mode',
        'strategy',
        'symbol',
        'route_type',
        'exchange',
        'buy_exchange',
        'sell_exchange',
        'spot_exchange',
        'futures_exchange',
    )

    def __init__(self, port: int, mode: str):
        self._port = port
        self._mode = mode
        self._started = False
        self._scan_duration = Histogram(
            'tradebot_scan_duration_seconds',
            'Scan cycle duration in seconds',
            ('mode',),
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
        )
        self._scan_total = Counter(
            'tradebot_scan_total',
            'Completed scan cycles',
            ('mode',),
        )
        self._scan_opportunities_total = Counter(
            'tradebot_scan_opportunities_total',
            'Signals found during completed scans',
            ('mode',),
        )
        self._scan_errors_total = Counter(
            'tradebot_scan_errors_total',
            'Errors captured during scans',
            ('mode',),
        )
        self._signal_total = Counter(
            'tradebot_signal_total',
            'Detected arbitrage signals',
            self._signal_labels,
        )
        self._signal_expected_profit_usdt_total = Counter(
            'tradebot_signal_expected_profit_usdt_total',
            'Accumulated expected signal profit in USDT',
            self._signal_labels,
        )
        self._signal_expected_profit_percent_total = Counter(
            'tradebot_signal_expected_profit_percent_total',
            'Accumulated expected signal profit percent',
            self._signal_labels,
        )
        self._trade_closed_total = Counter(
            'tradebot_trade_closed_total',
            'Closed trades',
            self._signal_labels,
        )
        self._trade_realized_profit_usdt_total = Counter(
            'tradebot_trade_realized_profit_usdt_total',
            'Accumulated positive realized profit in USDT for closed trades',
            self._signal_labels,
        )
        self._trade_realized_loss_usdt_total = Counter(
            'tradebot_trade_realized_loss_usdt_total',
            'Accumulated absolute realized loss in USDT for closed trades',
            self._signal_labels,
        )
        self._exchange_signal_total = Counter(
            'tradebot_signal_exchange_total',
            'Exchange participation in detected signals',
            ('mode', 'strategy', 'exchange', 'market_role', 'route_type'),
        )
        self._bot_running = Gauge(
            'tradebot_bot_running',
            'Whether the bot loop is currently running',
            ('mode',),
        )
        self._open_positions = Gauge(
            'tradebot_open_positions',
            'Open futures-spot positions',
            ('mode',),
        )
        self._exchange_balance_usdt = Gauge(
            'tradebot_exchange_balance_usdt',
            'Estimated total account balance in USDT by exchange',
            ('mode', 'exchange'),
        )
        self._total_balance_usdt = Gauge(
            'tradebot_total_balance_usdt',
            'Estimated total account balance in USDT across tracked exchanges',
            ('mode',),
        )
        self._last_scan_duration_ms = Gauge(
            'tradebot_last_scan_duration_ms',
            'Duration of the most recent completed scan in milliseconds',
            ('mode',),
        )
        self._last_scan_timestamp = Gauge(
            'tradebot_last_scan_timestamp_seconds',
            'Unix timestamp of the most recent completed scan',
            ('mode',),
        )
        self._errors_total = Counter(
            'tradebot_errors_total',
            'Application errors',
            ('mode', 'stage', 'exchange', 'symbol'),
        )

    def start(self) -> None:
        if self._started:
            return
        start_http_server(self._port)
        self._started = True

    def set_bot_running(self, is_running: bool) -> None:
        self._bot_running.labels(mode=self._mode).set(1 if is_running else 0)

    def set_open_positions(self, total: int) -> None:
        self._open_positions.labels(mode=self._mode).set(total)

    def set_exchange_balance(self, exchange: str, total_balance_usdt: float) -> None:
        self._exchange_balance_usdt.labels(mode=self._mode, exchange=exchange).set(total_balance_usdt)

    def set_total_balance(self, total_balance_usdt: float) -> None:
        self._total_balance_usdt.labels(mode=self._mode).set(total_balance_usdt)

    def record_scan(self, telemetry: ScanTelemetry) -> None:
        self._scan_total.labels(mode=self._mode).inc()
        self._scan_duration.labels(mode=self._mode).observe(telemetry.duration_ms / 1000)
        self._scan_opportunities_total.labels(mode=self._mode).inc(telemetry.opportunities_count)
        self._scan_errors_total.labels(mode=self._mode).inc(telemetry.errors_count)
        self._last_scan_duration_ms.labels(mode=self._mode).set(telemetry.duration_ms)
        self._last_scan_timestamp.labels(mode=self._mode).set(telemetry.scanned_at.timestamp())

    def record_signal(self, telemetry: SignalTelemetry) -> None:
        labels = self._signal_label_values(telemetry)
        self._signal_total.labels(**labels).inc()
        self._signal_expected_profit_usdt_total.labels(**labels).inc(telemetry.expected_profit_usdt)
        self._signal_expected_profit_percent_total.labels(**labels).inc(telemetry.expected_profit_percent)
        self._record_exchange_signal(telemetry)

    def record_trade(self, telemetry: TradeTelemetry) -> None:
        labels = self._signal_label_values(telemetry)
        self._trade_closed_total.labels(**labels).inc()
        if telemetry.realized_profit_usdt >= 0:
            self._trade_realized_profit_usdt_total.labels(**labels).inc(telemetry.realized_profit_usdt)
        else:
            self._trade_realized_loss_usdt_total.labels(**labels).inc(abs(telemetry.realized_profit_usdt))

    def record_error(self, stage: str, exchange: str = '', symbol: str = '') -> None:
        self._errors_total.labels(mode=self._mode, stage=stage, exchange=exchange, symbol=symbol).inc()

    def _signal_label_values(self, telemetry: SignalTelemetry | TradeTelemetry) -> dict[str, str]:
        data = asdict(telemetry)
        values = {label: str(data.get(label, '')) for label in self._signal_labels if label != 'mode'}
        values['mode'] = self._mode
        return values

    def _record_exchange_signal(self, telemetry: SignalTelemetry) -> None:
        if telemetry.strategy == 'cross_exchange':
            self._exchange_signal_total.labels(
                mode=self._mode,
                strategy=telemetry.strategy,
                exchange=telemetry.buy_exchange,
                market_role='spot_buy',
                route_type=telemetry.route_type,
            ).inc()
            self._exchange_signal_total.labels(
                mode=self._mode,
                strategy=telemetry.strategy,
                exchange=telemetry.sell_exchange,
                market_role='spot_sell',
                route_type=telemetry.route_type,
            ).inc()
            return

        if telemetry.strategy == 'futures_spot':
            self._exchange_signal_total.labels(
                mode=self._mode,
                strategy=telemetry.strategy,
                exchange=telemetry.spot_exchange,
                market_role='spot',
                route_type=telemetry.route_type,
            ).inc()
            self._exchange_signal_total.labels(
                mode=self._mode,
                strategy=telemetry.strategy,
                exchange=telemetry.futures_exchange,
                market_role='futures',
                route_type=telemetry.route_type,
            ).inc()
            return

        if telemetry.strategy == 'futures_funding':
            self._exchange_signal_total.labels(
                mode=self._mode,
                strategy=telemetry.strategy,
                exchange=telemetry.buy_exchange,
                market_role='futures_long',
                route_type=telemetry.route_type,
            ).inc()
            self._exchange_signal_total.labels(
                mode=self._mode,
                strategy=telemetry.strategy,
                exchange=telemetry.sell_exchange,
                market_role='futures_short',
                route_type=telemetry.route_type,
            ).inc()
            return

        self._exchange_signal_total.labels(
            mode=self._mode,
            strategy=telemetry.strategy,
            exchange=telemetry.exchange,
            market_role='triangular',
            route_type=telemetry.route_type,
        ).inc()
