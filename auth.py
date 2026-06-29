#!/usr/bin/env python3
"""
Authentication + role-based permissions for the hosted app.

Roles: admin, sales, fulfillment. Each route is guarded by a permission; the
business data is also redacted per role (Sales/Fulfillment never see profit/cost;
Fulfillment never sees money totals).
"""

from functools import wraps

from flask import abort
from flask_login import LoginManager, UserMixin, current_user
from werkzeug.security import generate_password_hash, check_password_hash

import db

login_manager = LoginManager()
login_manager.login_view = "login"

# Permission sets per role.
ROLE_PERMS = {
    "admin": {"view_orders", "view_money", "view_cost", "view_pnl", "edit_order",
              "edit_fulfillment", "view_customers", "manage_customers",
              "manage_users", "admin_actions"},
    # Sales is a limited console — Quote + To-order only (see the UI nav lock in
    # web/index.html). view_orders lets the two pages load; view_money shows prices.
    "sales": {"view_orders", "view_money"},
    "fulfillment": {"view_orders", "edit_fulfillment", "view_customers"},
}
ROLES = list(ROLE_PERMS.keys())


class User(UserMixin):
    def __init__(self, row):
        self.id = row["id"]
        self.username = row["username"]
        self.role = row["role"]
        self.name = row.get("name") or row["username"]

    @property
    def perms(self):
        return ROLE_PERMS.get(self.role, set())

    def has(self, perm):
        return perm in self.perms

    def as_dict(self):
        return {"id": self.id, "username": self.username, "role": self.role,
                "name": self.name, "perms": sorted(self.perms)}


@login_manager.user_loader
def load_user(uid):
    try:
        row = db.get_user_by_id(int(uid))
    except (ValueError, TypeError):
        return None
    return User(row) if row and row.get("active") else None


def verify(username, password):
    row = db.get_user(username)
    if row and check_password_hash(row["password_hash"], password):
        return User(row)
    return None


def hash_pw(password):
    # pbkdf2 is always available; werkzeug's default (scrypt) needs OpenSSL
    # support the system Python may lack.
    return generate_password_hash(password, method="pbkdf2:sha256")


def require(perm):
    """Route decorator: 401 if not logged in, 403 if the role lacks `perm`."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **k):
            if not current_user.is_authenticated:
                abort(401)
            if not current_user.has(perm):
                abort(403)
            return fn(*a, **k)
        return wrapper
    return deco


def actor():
    """Current user as a plain dict for the audit log."""
    if current_user.is_authenticated:
        return {"id": current_user.id, "username": current_user.username}
    return {}
