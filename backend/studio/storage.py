"""Maintain render disk usage outside of Library requests."""

import logging
import os
from datetime import timedelta
from pathlib import Path
from stat import S_ISREG

from django.db import DatabaseError, connections
from django.db.models import F, Q
from django.utils import timezone

from .exports import job_directory
from .models import Job


logger = logging.getLogger(__name__)
ACTIVE_REFRESH_INTERVAL = timedelta(seconds=30)
BATCH_SIZE = 16


def directory_usage(directory):
    """Return total and working file bytes in one walk, ignoring symlinks."""
    if directory.is_symlink():
        return 0, 0
    total = working = 0
    work = directory / "work"
    for folder, _, names in os.walk(directory, followlinks=False):
        size = 0
        for name in names:
            try:
                entry = os.stat(os.path.join(folder, name), follow_symlinks=False)
            except OSError:
                continue
            if S_ISREG(entry.st_mode):
                size += entry.st_size
        total += size
        if Path(folder).is_relative_to(work):
            working += size
    return total, working


def storage_values(job_id):
    measured_at = timezone.now()
    total, working = directory_usage(job_directory(job_id))
    return {"storage_bytes": total, "work_bytes": working, "storage_measured_at": measured_at}


def mark_storage_changed(job_id):
    Job.objects.filter(pk=job_id).update(updated_at=timezone.now())


def refresh_storage_batch():
    cutoff = timezone.now() - ACTIVE_REFRESH_INTERVAL
    candidates = list(Job.objects.filter(
        Q(storage_measured_at__isnull=True) | Q(updated_at__gt=F("storage_measured_at")),
    ).exclude(
        status__in=Job.ACTIVE, storage_measured_at__gt=cutoff,
    ).order_by(F("storage_measured_at").asc(nulls_first=True), "id").values_list(
        "pk", "status", "updated_at", "storage_measured_at",
    )[:BATCH_SIZE])
    for job_id, status, updated_at, measured_at in candidates:
        values = storage_values(job_id)
        current = Job.objects.filter(pk=job_id, status=status, storage_measured_at=measured_at)
        if status not in Job.ACTIVE:
            current = current.filter(updated_at=updated_at)
        current.update(**values)
    return len(candidates)


def monitor_storage(stop):
    try:
        while not stop.is_set():
            try:
                refresh_storage_batch()
            except (DatabaseError, OSError) as exc:
                logger.warning("Render storage accounting will retry (%s)", type(exc).__name__)
                connections.close_all()
            stop.wait(2)
    finally:
        connections.close_all()
