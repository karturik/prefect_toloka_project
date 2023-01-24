import attr
from typing import Dict, Optional


@attr.s(frozen=True)
class Answer:
    task_id: str = attr.ib()
    assignment_id: str = attr.ib()
    input_values: Dict = attr.ib(default=None)
    user_id: Optional[str] = attr.ib(default=None)
    output_values: Optional[Dict] = attr.ib(default=None)
