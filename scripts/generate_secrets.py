#!/usr/bin/env python3
"""Print strong random values for JWT_SECRET and ENCRYPTION_MASTER_KEY."""

import secrets

if __name__ == "__main__":
    print(f"JWT_SECRET={secrets.token_urlsafe(48)}")
    print(f"ENCRYPTION_MASTER_KEY={secrets.token_urlsafe(48)}")
