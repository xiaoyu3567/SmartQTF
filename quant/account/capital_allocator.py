from quant.schemas.portfolio import (
    CapitalAllocationDecision,
    CapitalAllocationRequest,
    CapitalBudgetDecision,
    CapitalBudgetRequest,
)


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


class CapitalBudgetAllocator:
    def allocate(self, request: CapitalBudgetRequest) -> CapitalBudgetDecision:
        base_risk_budget = request.account_equity * request.base_risk_budget_pct
        confidence_multiplier = self._confidence_multiplier(request)
        volatility_multiplier = self._volatility_multiplier(request)
        correlation_multiplier = self._correlation_multiplier(request)
        scaled_risk_budget = (
            base_risk_budget
            * confidence_multiplier
            * volatility_multiplier
            * correlation_multiplier
        )

        max_symbol_notional = self._remaining_capacity(
            request.account_equity,
            request.max_symbol_weight,
            request.current_symbol_notional,
        )
        max_total_notional = self._remaining_capacity(
            request.account_equity,
            request.max_total_weight,
            request.current_total_notional,
        )
        if request.correlation_group is None:
            max_group_notional = max_total_notional
        else:
            max_group_notional = self._remaining_capacity(
                request.account_equity,
                request.max_correlation_group_weight,
                request.current_correlation_group_notional,
            )

        caps = {
            "free_margin": request.free_margin,
            "max_symbol_notional": max_symbol_notional,
            "max_total_notional": max_total_notional,
            "max_group_notional": max_group_notional,
        }
        adjusted_risk_budget = min([scaled_risk_budget] + list(caps.values()))
        reason_codes = self._reason_codes(
            request=request,
            scaled_risk_budget=scaled_risk_budget,
            adjusted_risk_budget=adjusted_risk_budget,
            caps=caps,
            confidence_multiplier=confidence_multiplier,
            volatility_multiplier=volatility_multiplier,
            correlation_multiplier=correlation_multiplier,
        )

        approved = (
            adjusted_risk_budget >= request.min_risk_budget_usdt
            and adjusted_risk_budget > 0.0
        )
        if approved:
            reason_codes.append("capital_budget_approved")
        else:
            adjusted_risk_budget = 0.0
            reason_codes.append("capital_budget_below_minimum")

        trade_intent = request.trade_intent
        return CapitalBudgetDecision(
            budget_id=request.budget_id,
            approved=approved,
            decision_id=trade_intent.decision_id,
            trade_intent_id=trade_intent.trade_intent_id,
            symbol=trade_intent.symbol,
            side=trade_intent.side,
            account_equity=request.account_equity,
            free_margin=request.free_margin,
            base_risk_budget_usdt=base_risk_budget,
            scaled_risk_budget_usdt=scaled_risk_budget,
            adjusted_risk_budget_usdt=adjusted_risk_budget,
            max_symbol_notional=max_symbol_notional,
            max_total_notional=max_total_notional,
            max_group_notional=max_group_notional,
            confidence_multiplier=confidence_multiplier,
            volatility_multiplier=volatility_multiplier,
            correlation_multiplier=correlation_multiplier,
            constraint_caps=caps,
            reason_codes=reason_codes,
            input_refs={
                "decision_id": trade_intent.decision_id,
                "trade_intent_id": trade_intent.trade_intent_id,
                "source_signal_id": trade_intent.source_signal_id,
                "correlation_group": request.correlation_group,
            },
            trace=request.trace or trade_intent.trace,
        )

    def _confidence_multiplier(self, request: CapitalBudgetRequest) -> float:
        if request.trade_intent.confidence is None:
            return 1.0
        return request.trade_intent.confidence

    def _volatility_multiplier(self, request: CapitalBudgetRequest) -> float:
        if request.volatility is None or request.target_volatility is None:
            return 1.0
        return min(1.0, request.target_volatility / request.volatility)

    def _correlation_multiplier(self, request: CapitalBudgetRequest) -> float:
        if request.correlation_group is None:
            return 1.0
        if request.current_correlation_group_notional <= 0.0:
            return 1.0
        return request.correlation_exposure_multiplier

    def _remaining_capacity(
        self,
        account_equity: float,
        max_weight: float,
        current_notional: float,
    ) -> float:
        return max(0.0, account_equity * max_weight - current_notional)

    def _reason_codes(
        self,
        request: CapitalBudgetRequest,
        scaled_risk_budget: float,
        adjusted_risk_budget: float,
        caps: dict[str, float],
        confidence_multiplier: float,
        volatility_multiplier: float,
        correlation_multiplier: float,
    ) -> list[str]:
        reason_codes = list(request.reason_codes)
        reason_codes.append("capital_budget_from_trade_intent")

        if confidence_multiplier < 1.0:
            reason_codes.append("confidence_scaled")
        if volatility_multiplier < 1.0:
            reason_codes.append("volatility_scaled")
        if correlation_multiplier < 1.0:
            reason_codes.append("correlation_exposure_scaled")

        if adjusted_risk_budget < scaled_risk_budget:
            reason_codes.append("capital_budget_capped")
            for name, cap_value in caps.items():
                if cap_value <= adjusted_risk_budget:
                    reason_codes.append(f"{name}_capped")
        return reason_codes
