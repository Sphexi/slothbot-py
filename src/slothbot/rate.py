"""Message rate value type."""

from dataclasses import dataclass

from slothbot.durations import humanize_seconds


@dataclass(frozen=True)
class Rate:
    """A count of messages over a window of seconds."""

    count: int
    seconds: int

    def __post_init__(self) -> None:
        if self.count < 0:
            raise ValueError("count must be >= 0")
        if self.seconds < 0:
            raise ValueError("seconds must be >= 0")

    @classmethod
    def per(cls, seconds: int, count: int) -> "Rate":
        return cls(count=count, seconds=seconds)

    @classmethod
    def per_minute(cls, count: int) -> "Rate":
        return cls(count=count, seconds=60)

    @property
    def per_second(self) -> float:
        return self.count / self.seconds

    def exceeds(self, other: "Rate") -> bool:
        return self.per_second > other.per_second

    def ratio_to(self, other: "Rate") -> float:
        return self.per_second / other.per_second

    def humanize(self) -> str:
        return f"{self.count} per {humanize_seconds(self.seconds)}"
