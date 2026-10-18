from django.contrib import admin

from ledger.chains.models import TaskChain
from ledger.readonly_admin import ReadOnlyModelAdmin


@admin.register(TaskChain)
class TaskChainAdmin(ReadOnlyModelAdmin[TaskChain]):
    list_display = ("name", "token", "lease_until")
