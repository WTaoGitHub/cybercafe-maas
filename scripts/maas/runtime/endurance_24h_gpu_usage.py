"""Purge inference usage for the exact disposable GPUStack user after revocation."""

import asyncio
import json
import re
import sys

import asyncpg
from gpustack.config.config import Config


TABLES = ('model_usages', 'model_usage_details', 'model_usage_details_archive')


async def cleanup(user_id, username):
    if not re.fullmatch(r'test_agent_endurance_[a-z0-9]{8,24}', username):
        raise ValueError('Dedicated GPUStack test user required')
    connection = await asyncpg.connect(host='127.0.0.1', port=Config.model_fields['database_port'].default,
                                       database='gpustack', user='root', timeout=5, command_timeout=10)
    try:
        async with connection.transaction():
            if not await connection.fetchval('''SELECT 1 FROM principals WHERE id=$1 AND name=$2
                    AND NOT is_admin AND deleted_at IS NULL''', user_id, username):
                raise RuntimeError('The GPUStack user does not match the run manifest')
            entries = {table: await connection.fetchval(f'SELECT count(*) FROM {table} WHERE user_id=$1', user_id)
                       for table in TABLES}
            for table in TABLES:
                await connection.execute(f'DELETE FROM {table} WHERE user_id=$1', user_id)
            if any([await connection.fetchval(f'SELECT 1 FROM {table} WHERE user_id=$1 LIMIT 1', user_id)
                    for table in TABLES]):
                raise RuntimeError('GPUStack test usage remains')
        return entries
    finally:
        await connection.close()


if __name__ == '__main__':
    print(json.dumps(asyncio.run(cleanup(int(sys.argv[1]), sys.argv[2]))))
