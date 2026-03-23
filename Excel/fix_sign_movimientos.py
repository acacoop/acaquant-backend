import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mongo_manager import get_mongo_client

client = get_mongo_client()
col = client['CashFlow']['Movimientos']
r = col.update_many({}, [{'$set': {'total': {'$multiply': ['$total', -1]}}}])
print('Modificados:', r.modified_count)
client.close()
