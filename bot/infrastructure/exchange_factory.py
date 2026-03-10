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
    def create_binance_spot(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.binance({**_creds(creds), 'options': {'defaultType': 'spot'}, 'enableRateLimit': True})
        return CcxtExchangeAdapter(ex, Fee.binance(), False)

    def create_binance_futures(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.binance({**_creds(creds), 'options': {'defaultType': 'future'}, 'enableRateLimit': True})
        return CcxtExchangeAdapter(ex, Fee(0.0002, 0.0004), True)

    def create_bybit(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.bybit({**_creds(creds), 'options': {'defaultType': 'spot'}, 'enableRateLimit': True})
        return CcxtExchangeAdapter(ex, Fee.bybit(), False)

    def create_bybit_futures(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.bybit({**_creds(creds), 'options': {'defaultType': 'linear'}, 'enableRateLimit': True})
        return CcxtExchangeAdapter(ex, Fee(0.0001, 0.0006), True)

    def create_okx(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.okx({**_creds(creds), 'options': {'defaultType': 'spot'}, 'enableRateLimit': True})
        return CcxtExchangeAdapter(ex, Fee.okx(), False)

    def create_kucoin(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.kucoin({**_creds(creds), 'enableRateLimit': True})
        return CcxtExchangeAdapter(ex, Fee.kucoin(), False)

    def create_gateio(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.gateio({
            **_creds(creds),
            'enableRateLimit': True,
            'timeout': 30000,
            'options': {
                'defaultType': 'spot',
                'fetchMarkets': {'types': ['spot']},
            },
        })
        return CcxtExchangeAdapter(ex, Fee.gateio(), False)

    def create_mexc(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.mexc({**_creds(creds), 'enableRateLimit': True})
        return CcxtExchangeAdapter(ex, Fee.mexc(), False)

    def create_bitget(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.bitget({**_creds(creds), 'enableRateLimit': True})
        return CcxtExchangeAdapter(ex, Fee.bitget(), False)

    def create_htx(self, creds: ExchangeCredentials | None = None) -> CcxtExchangeAdapter:
        ex = ccxt.htx({**_creds(creds), 'enableRateLimit': True})
        return CcxtExchangeAdapter(ex, Fee.htx(), False)
