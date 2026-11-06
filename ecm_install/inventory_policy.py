"""Application-owned whole-distribution guards; not platform or file-size limits.

The combined JP6/JP7 baseline contains 610 files, 196065077 bytes and 684
entries. These finite budgets include headroom for the integration and docs.
Both consumers still hash every distributed dependency generation.
"""
MAX_INVENTORY_FILES = 768
MAX_INVENTORY_BYTES = 256 * 1024 * 1024
MAX_INVENTORY_ENTRIES = 2048
