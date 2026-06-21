from abc import ABC, abstractmethod
from dataclasses import dataclass
from algotrading.models import Candle, Position


@dataclass
class AgentView:
    stance: str        # "BULLISH" | "BEARISH" | "NEUTRAL"
    confidence: float
    reason: str


class Agent(ABC):
    name: str = "agent"

    @abstractmethod
    def assess(self, candles: list[Candle], position: Position | None) -> AgentView:
        raise NotImplementedError
