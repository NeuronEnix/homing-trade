from algotrading.models import Candle, Position


class Broker:
    def __init__(self, fee: float, slippage: float):
        self.fee = fee
        self.slippage = slippage

    def fill_price(self, price: float, side: str, is_entry: bool) -> float:
        # Determine whether this fill is a buy or a sell.
        buying = (side == "LONG" and is_entry) or (side == "SHORT" and not is_entry)
        if buying:
            return price * (1 + self.slippage)
        return price * (1 - self.slippage)

    def position_size(self, balance, entry_price, risk_pct, stop_pct, leverage) -> tuple[float, float]:
        size = (balance * risk_pct) / (entry_price * stop_pct)
        margin = size * entry_price / leverage
        if margin > balance:
            margin = balance
            size = balance * leverage / entry_price
        return size, margin

    def stop_price(self, entry_price: float, side: str, stop_pct: float) -> float:
        if side == "LONG":
            return entry_price * (1 - stop_pct)
        return entry_price * (1 + stop_pct)

    def entry_fee(self, size: float, fill: float) -> float:
        return size * fill * self.fee

    def realized_pnl(self, position: Position, exit_fill: float) -> float:
        if position.side == "LONG":
            return position.size * (exit_fill - position.entry_price)
        return position.size * (position.entry_price - exit_fill)

    def unrealized_pnl(self, position: Position, price: float) -> float:
        if position.side == "LONG":
            return position.size * (price - position.entry_price)
        return position.size * (position.entry_price - price)

    def liquidation_price(self, position: Position) -> float:
        if position.side == "LONG":
            return position.entry_price * (1 - 1 / position.leverage)
        return position.entry_price * (1 + 1 / position.leverage)

    def hit_stop(self, position: Position, candle: Candle) -> bool:
        if position.side == "LONG":
            return candle.low <= position.stop_price
        return candle.high >= position.stop_price

    def hit_liquidation(self, position: Position, candle: Candle) -> bool:
        liq = self.liquidation_price(position)
        if position.side == "LONG":
            return candle.low <= liq
        return candle.high >= liq
