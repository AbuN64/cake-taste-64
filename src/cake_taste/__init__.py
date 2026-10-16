"""Cake Taste: measured experiments over explicit project profiles."""

__version__ = "0.1.0"


class TasteError(Exception):
    """A named refusal that the public command can report without guessing a cause."""

    def __init__(self, key: str, detail: str) -> None:
        super().__init__(detail)
        self.key = key
        self.detail = detail

    def finding(self) -> dict[str, object]:
        return {"key": self.key, "message": self.detail, "blocking": True}
