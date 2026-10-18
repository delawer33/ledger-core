"""Base for the admin: every model is visible and nothing can be added, changed or deleted."""
# ruff: noqa: ARG002

from typing import Any

from django.contrib import admin
from django.db.models import Model
from django.http import HttpRequest


class ReadOnlyModelAdmin[M: Model](admin.ModelAdmin[M]):
    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_view_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return request.user.is_active and request.user.is_staff


class ReadOnlyInline(admin.TabularInline[Any, Any]):
    extra = 0
    can_delete = False

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False
