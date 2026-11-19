"""Dispatch scopes are recorded before send and survive catalog and route changes."""


def unresolved(connection):
    return connection.execute("""SELECT t.model,b.dispatch_route,b.dispatch_pool_id,b.dispatch_gpu_uuid,b.gpu_uuid
          FROM backend_activity b JOIN tasks t ON t.id=b.task_id WHERE b.state='UNKNOWN'
          UNION ALL SELECT model,dispatch_route,dispatch_pool_id,dispatch_gpu_uuid,gpu_uuid
          FROM sync_activity WHERE state='UNKNOWN'""").fetchall()


def blocks(rows, name, route, pool_id, gpu_uuid=None):
    for row in rows:
        if row['dispatch_route'] is None:
            return True
        pinned = row['dispatch_gpu_uuid']
        if pinned and (gpu_uuid is None or gpu_uuid == pinned):
            return True
        if pinned and pinned == row['gpu_uuid']:
            continue
        if gpu_uuid and not pinned and row['gpu_uuid'] is None:
            # A legacy unlocated attempt could occupy a GPU shared across pools.
            return True
        if (row['model'] == name or (route and row['dispatch_route'] == route)
                or (pool_id is not None and row['dispatch_pool_id'] == pool_id)
                or (gpu_uuid and gpu_uuid == row['gpu_uuid'])):
            return True
    return False
