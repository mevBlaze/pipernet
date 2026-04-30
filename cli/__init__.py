"""pipernet — reference CLI client implementing schema v2.0."""
from .core import (
    canonical,
    generate_keypair,
    load_private_key,
    load_pubkey_registry,
    register_pubkey,
    sign_envelope,
    verify_envelope,
    build_envelope,
    append_to_channel,
    read_channel,
)

__version__ = "0.1.0"
