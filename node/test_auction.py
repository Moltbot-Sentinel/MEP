import asyncio
import websockets
import json
import requests
import uuid

HUB_URL = "http://localhost:8000"

async def test():
    provider = f'mep-provider-{uuid.uuid4().hex[:6]}'
    requests.post(f'{HUB_URL}/register', json={'pubkey': provider})
    async with websockets.connect(f'ws://localhost:8000/ws/{provider}') as ws:
        consumer = 'test-consumer'
        requests.post(f'{HUB_URL}/register', json={'pubkey': consumer})
        requests.post(f'{HUB_URL}/tasks/submit', json={'consumer_id': consumer, 'payload': 'Test payload', 'bounty': 1.0})
        
        msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
        data = json.loads(msg)
        print('Received:', data)
        
        if data['event'] == 'rfc':
            task_id = data['data']['id']
            resp = requests.post(f'{HUB_URL}/tasks/bid', json={'task_id': task_id, 'provider_id': provider})
            print('Bid response:', resp.json())
            
            complete_resp = requests.post(f'{HUB_URL}/tasks/complete', json={
                'task_id': task_id, 
                'provider_id': provider, 
                'result_payload': 'Done!'
            })
            print('Complete response:', complete_resp.json())

if __name__ == '__main__':
    asyncio.run(test())
