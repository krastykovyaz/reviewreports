"""Small SQLite helpers shared by the stores."""

# How long a connection waits for another writer before "database is locked". The default (5 s) is shorter than a VACUUM of a
# database full of photo/original BLOBs, so a purge could make concurrent requests fail.
BUSY_TIMEOUT_SEC = 30

# VACUUM rewrites the whole file under an exclusive lock. Only worth doing when a meaningful amount of space is actually free.
VACUUM_MIN_FREE_BYTES = 16 * 1024 * 1024


async def maybe_vacuum(db, min_free_bytes: int = None) -> bool:
    """VACUUM only when the free pages add up to min_free_bytes or more (it used to run after EVERY purge, however little
    had been deleted). Returns whether it ran."""
    threshold = VACUUM_MIN_FREE_BYTES if min_free_bytes is None else min_free_bytes
    free = (await (await db.execute("PRAGMA freelist_count")).fetchone())[0]
    page = (await (await db.execute("PRAGMA page_size")).fetchone())[0]
    if free * page >= threshold:
        await db.execute("VACUUM")
        return True
    return False
