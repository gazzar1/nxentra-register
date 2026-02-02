# accounting/consumers.py
import json
from channels.generic.websocket import AsyncWebsocketConsumer

class AccountConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("accounts", self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("accounts", self.channel_name)

    async def receive(self, text_data):
        data = json.loads(text_data)
        # Optional: handle incoming messages

    async def send_account_update(self, event):
        await self.send(text_data=json.dumps({
            "message": event["data"]["message"]
        }))
