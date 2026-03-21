from __future__ import annotations

import ccxt.async_support as ccxt

from ..config import ExchangeCredentials
from ..domain.value_objects import Fee
from .ccxt_adapter import CcxtExchangeAdapter


def _creds(c: ExchangeCredentials | None) -> dict:
    if not c or not c.api_key:
        return {}
    d: dict = {'apiKey': c.api_key, 'secret': c.secret}
    if c.passphrase:
        d['password'] = c.passphrase
    return d


class ExchangeFactory:
    def __init__(self, timeout_ms: int = 20000):
        self._timeout_ms = max(timeout_ms, 1000)

    def _base_options(self, creds: ExchangeCredentials | None = None) -> dict:
        return {
            **_creds(creds),
            'enableRateLimit': True,
            'timeout': self._timeout_ms,
        }

    def create_binance_spot(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.binance({**self._base_options(creds), 'options': {'defaultType': 'spot'}})
        return CcxtExchangeAdapter(ex, Fee.binance(), False)

    def create_binance_futures(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.binance({**self._base_options(creds), 'options': {'defaultType': 'future'}})
        return CcxtExchangeAdapter(ex, Fee(0.0002, 0.0004), True)

    def create_bybit(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.bybit({**self._base_options(creds), 'options': {'defaultType': 'spot'}})
        return CcxtExchangeAdapter(ex, Fee.bybit(), False)

    def create_bybit_futures(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.bybit({**self._base_options(creds), 'options': {'defaultType': 'linear'}})
        return CcxtExchangeAdapter(ex, Fee(0.0001, 0.0006), True)

    def create_okx(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.okx({**self._base_options(creds), 'options': {'defaultType': 'spot'}})
        return CcxtExchangeAdapter(ex, Fee.okx(), False)

    def create_okx_futures(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.okx({**self._base_options(creds), 'options': {'defaultType': 'swap'}})
        return CcxtExchangeAdapter(ex, Fee(0.0002, 0.0005), True)

    def create_kucoin(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.kucoin(self._base_options(creds))
        return CcxtExchangeAdapter(ex, Fee.kucoin(), False)

    def create_kucoin_futures(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.kucoinfutures(self._base_options(creds))
        return CcxtExchangeAdapter(ex, Fee(0.0002, 0.0006), True, exchange_id='kucoin')

    def create_gateio(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.gateio({
            **self._base_options(creds),
            'timeout': max(self._timeout_ms, 30000),
            'options': {
                'defaultType': 'spot',
                'fetchMarkets': {'types': ['spot']},
            },
        })
        return CcxtExchangeAdapter(ex, Fee.gateio(), False)

    def create_gateio_futures(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.gateio({
            **self._base_options(creds),
            'timeout': max(self._timeout_ms, 30000),
            'options': {
                'defaultType': 'swap',
                'fetchMarkets': {'types': ['swap']},
            },
        })
        return CcxtExchangeAdapter(ex, Fee(0.0002, 0.0005), True)

    def create_mexc(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.mexc(self._base_options(creds))
        return CcxtExchangeAdapter(ex, Fee.mexc(), False)

    def create_mexc_futures(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.mexc({**self._base_options(creds), 'options': {'defaultType': 'swap'}})
        return CcxtExchangeAdapter(ex, Fee(0.0, 0.0002), True)

    def create_bitget(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.bitget(self._base_options(creds))
        return CcxtExchangeAdapter(ex, Fee.bitget(), False)

    def create_bitget_futures(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.bitget({**self._base_options(creds), 'options': {'defaultType': 'swap'}})
        return CcxtExchangeAdapter(ex, Fee(0.0002, 0.0006), True)

    def create_htx(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.htx(self._base_options(creds))
        return CcxtExchangeAdapter(ex, Fee.htx(), False)

    def create_htx_futures(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.htx({**self._base_options(creds), 'options': {'defaultType': 'swap'}})
        return CcxtExchangeAdapter(ex, Fee(0.0002, 0.0005), True)
