"""Dispatch scopes are recorded before send and survive catalog and route changes."""


def unresolved(connection):
    return connection.execute("""SELECT t.model,b.dispatch_route,b.dispatch_pool_id
          FROM backend_activity b JOIN tasks t ON t.id=b.task_id WHERE b.state='UNKNOWN'
          UNION ALL SELECT model,dispatch_route,dispatch_pool_id FROM sync_activity WHERE state='UNKNOWN'""").fetchall()


def blocks(rows, name, route, pool_id):
    return any(row['dispatch_route'] is None or row['model'] == name
               or (route and row['dispatch_route'] == route)
               or (pool_id is not None and row['dispatch_pool_id'] == pool_id)
               for row in rows)
