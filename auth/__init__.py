"""Auth package — friend username/password login + the admin TOTP gate."""

from auth.blueprint import auth_bp

__all__ = ["auth_bp"]
