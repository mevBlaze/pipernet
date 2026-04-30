"""
middle-out: a research attempt at Hutter Prize-class compression.

Public API (v0):
    encode(data: bytes) -> bytes
    decode(data: bytes) -> bytes
"""

from .baseline import encode, decode

__all__ = ["encode", "decode"]
__version__ = "0.1.0"
