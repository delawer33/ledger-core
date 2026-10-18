"""Input limits of the core (ADR-0014). Plain constants, so services and schemas share them."""

# Largest absolute Amount of one entry, hold or capture, in minor units. A bigint holds 9.2e18; the
# margin leaves room to sum several maximal entries before a Posted Balance can overflow.
MAX_AMOUNT = 10**18

# Longest Hold lifetime: one year. Far below the point where `now + ttl` overflows a datetime.
MAX_TTL_SECONDS = 365 * 24 * 3600

# Idempotency-Key and X-Source-System are stored in varchar(255) columns.
MAX_HEADER_LENGTH = 255

# Bounds of a bigint Posted Balance.
BALANCE_MIN = -(2**63)
BALANCE_MAX = 2**63 - 1
