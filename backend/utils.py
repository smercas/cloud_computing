from typing import Protocol


def identity[E](e: E) -> E: return e
class HasValueProperty(Protocol):
  @property
  def value(self) -> str: pass
def to_value[E: HasValueProperty](e: E) -> str: return e.value
