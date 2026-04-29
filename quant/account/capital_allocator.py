from quant.schemas.portfolio import CapitalAllocationDecision, CapitalAllocationRequest


class CapitalAllocator:
    def allocate(self, request: CapitalAllocationRequest) -> CapitalAllocationDecision:
        desired_notional = self._desired_notional(request)
        reason_codes = list(request.reason_codes)
        if request.allocation_mode in {"kelly", "kelly_volatility_target"}:
            reason_codes.append("kelly_scaled")

        volatility = self._effective_volatility(request)
        if volatility is not None and request.target_volatility is not None:
            volatility_scale = min(1.0, request.target_volatility / volatility)
            desired_notional *= volatility_scale
            reason_codes.append("volatility_scaled")

        symbol_cap = max(
            0.0,
            request.account_equity * request.max_symbol_weight - request.current_symbol_notional,
        )
        cash_cap = request.available_cash
        allocated_notional = min(desired_notional, symbol_cap, cash_cap)

        if allocated_notional < request.min_notional or allocated_notional <= 0.0:
            return CapitalAllocationDecision(
                allocation_id=request.allocation_id,
                approved=False,
                symbol=request.symbol,
                side=request.side,
                quantity=0.0,
                notional=0.0,
                price=request.price,
                reason_codes=reason_codes + ["allocation_below_minimum"],
                trace=request.trace,
            )

        if allocated_notional < desired_notional:
            reason_codes.append("allocation_capped")
        else:
            reason_codes.append("allocation_approved")

        quantity = allocated_notional / request.price
        return CapitalAllocationDecision(
            allocation_id=request.allocation_id,
            approved=True,
            symbol=request.symbol,
            side=request.side,
            quantity=quantity,
            notional=allocated_notional,
            price=request.price,
            reason_codes=reason_codes,
            trace=request.trace,
        )

    def _desired_notional(self, request: CapitalAllocationRequest) -> float:
        if request.allocation_mode in {"kelly", "kelly_volatility_target"}:
            kelly_fraction = self._kelly_fraction(request)
            return request.account_equity * kelly_fraction * request.strategy_weight

        return request.account_equity * request.target_weight * request.strategy_weight

    def _kelly_fraction(self, request: CapitalAllocationRequest) -> float:
        loss_rate = 1.0 - request.win_rate
        raw_fraction = request.win_rate - loss_rate / request.payoff_ratio
        capped_fraction = min(request.max_kelly_fraction, max(0.0, raw_fraction))

        if request.signal_confidence is None:
            return capped_fraction
        return capped_fraction * request.signal_confidence

    def _effective_volatility(self, request: CapitalAllocationRequest):
        if request.volatility is not None:
            return request.volatility
        if request.atr is not None:
            return request.atr / request.price
        return None
