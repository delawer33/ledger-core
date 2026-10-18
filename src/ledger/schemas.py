"""Request and response schemas shared across routers."""

from typing import Annotated

from pydantic import Field, StrictInt

# Amount is a signed 64-bit integer in minor units (ADR-0001); floats and strings are rejected.
Amount = Annotated[StrictInt, Field(ge=-(2**63), le=2**63 - 1)]
