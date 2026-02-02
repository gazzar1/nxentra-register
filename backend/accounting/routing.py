from django.urls import path
from .consumers import AccountConsumer

websocket_urlpatterns = [
    path("ws/accounts/", AccountConsumer.as_asgi()),
]
