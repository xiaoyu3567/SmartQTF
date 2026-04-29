import time
from typing import Iterable, List, Optional

from quant.schemas.universe import (
    UniverseFilterConfig,
    UniverseInstrument,
    UniverseRejection,
    UniverseSnapshot,
)


def build_universe_snapshot(
    instruments: Iterable[UniverseInstrument],
    filter_config: Optional[UniverseFilterConfig] = None,
    *,
    as_of_timestamp: Optional[int] = None,
    source: str = "market_metadata",
) -> UniverseSnapshot:
    config = filter_config or UniverseFilterConfig()
    timestamp = int(as_of_timestamp if as_of_timestamp is not None else time.time())
    selected, rejected = filter_universe_instruments(instruments, config)
    return UniverseSnapshot(
        snapshot_id=f"{config.venue}-{config.instrument_type.lower()}-universe-{timestamp}",
        venue=config.venue,
        instrument_type=config.instrument_type,
        as_of_timestamp=timestamp,
        source=source,
        filters=config,
        instruments=selected,
        rejected=rejected,
    )


def filter_universe_instruments(
    instruments: Iterable[UniverseInstrument],
    config: UniverseFilterConfig,
) -> tuple[List[UniverseInstrument], List[UniverseRejection]]:
    selected: List[UniverseInstrument] = []
    rejected: List[UniverseRejection] = []

    allowed_quotes = _normalized_set(config.quote_currencies)
    allowed_statuses = {status.strip().lower() for status in config.allowed_statuses}
    blacklist = _normalized_set(config.blacklist)
    target_venue = config.venue.strip().lower()
    target_instrument_type = config.instrument_type.strip().upper()

    for instrument in instruments:
        reasons = _rejection_reasons(
            instrument,
            config=config,
            target_venue=target_venue,
            target_instrument_type=target_instrument_type,
            allowed_quotes=allowed_quotes,
            allowed_statuses=allowed_statuses,
            blacklist=blacklist,
        )
        if reasons:
            rejected.extend(
                UniverseRejection(
                    symbol=instrument.symbol,
                    reason_code=code,
                    reason=reason,
                )
                for code, reason in reasons
            )
            continue
        selected.append(instrument)

    selected.sort(
        key=lambda item: (
            -(item.turnover_24h or 0.0),
            -(item.volume_24h or 0.0),
            item.symbol,
        )
    )
    return selected, rejected


def _rejection_reasons(
    instrument: UniverseInstrument,
    *,
    config: UniverseFilterConfig,
    target_venue: str,
    target_instrument_type: str,
    allowed_quotes: set[str],
    allowed_statuses: set[str],
    blacklist: set[str],
) -> List[tuple[str, str]]:
    reasons: List[tuple[str, str]] = []
    symbol = instrument.symbol.strip().upper()

    if instrument.venue.strip().lower() != target_venue:
        reasons.append(("venue_mismatch", "instrument venue does not match universe venue"))
    if instrument.instrument_type.strip().upper() != target_instrument_type:
        reasons.append(("instrument_type_mismatch", "instrument type does not match universe type"))
    if instrument.status.strip().lower() not in allowed_statuses:
        reasons.append(("status_not_allowed", "instrument status is not allowed"))
    if instrument.quote_currency.strip().upper() not in allowed_quotes:
        reasons.append(("quote_currency_not_allowed", "quote currency is not allowed"))
    if symbol in blacklist:
        reasons.append(("blacklisted_symbol", "symbol is blacklisted"))

    if config.require_order_rules:
        if instrument.quantity_step <= 0.0:
            reasons.append(("missing_quantity_step", "instrument is missing quantity step"))
        if instrument.min_quantity <= 0.0:
            reasons.append(("missing_min_quantity", "instrument is missing minimum quantity"))

    if config.max_min_quantity is not None and instrument.min_quantity > config.max_min_quantity:
        reasons.append(("min_quantity_too_large", "minimum order quantity is above configured limit"))
    if config.max_min_notional is not None and instrument.min_notional > config.max_min_notional:
        reasons.append(("min_notional_too_large", "minimum notional is above configured limit"))

    if config.min_volume_24h > 0.0:
        if instrument.volume_24h is None:
            reasons.append(("missing_volume_24h", "24h volume is required for liquidity filtering"))
        elif instrument.volume_24h < config.min_volume_24h:
            reasons.append(("volume_below_minimum", "24h volume is below configured threshold"))

    if config.min_turnover_24h > 0.0:
        if instrument.turnover_24h is None:
            reasons.append(("missing_turnover_24h", "24h turnover is required for liquidity filtering"))
        elif instrument.turnover_24h < config.min_turnover_24h:
            reasons.append(("turnover_below_minimum", "24h turnover is below configured threshold"))

    return reasons


def _normalized_set(values: Iterable[str]) -> set[str]:
    return {str(value).strip().upper() for value in values if str(value).strip()}

