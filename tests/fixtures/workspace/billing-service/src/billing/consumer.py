import httpx
from aiokafka import AIOKafkaConsumer


async def run():
    consumer = AIOKafkaConsumer("orders.placed")
    async for _msg in consumer:
        await httpx.AsyncClient().get("http://orders-service/orders/42")
        consumer.subscribe("orders.placed")
