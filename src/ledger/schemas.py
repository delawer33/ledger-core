"""Request and response schemas shared across routers."""

from typing import Annotated

from pydantic import Field, StrictInt

from ledger.limits import MAX_AMOUNT

# Amount is an integer in minor units (ADR-0001), at most MAX_AMOUNT in absolute value (ADR-0014);
# floats and strings are rejected. Input only: output schemas use plain int (ADR-0014).
Amount = Annotated[
    StrictInt,
    Field(
        ge=-MAX_AMOUNT,
        le=MAX_AMOUNT,
        description=f"Integer minor units, absolute value at most {MAX_AMOUNT}.",
    ),
]
