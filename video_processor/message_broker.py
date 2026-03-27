import json
import logging
import time

import pika


class MessageBrokerClient:
    def __init__(self, host: str, queue_name: str):
        self.host = host
        self.queue_name = queue_name
        self.connection = None
        self.channel = None

    def connect(self):
        """Connects to RabbitMQ with a robust retry loop for Docker boot times."""
        retries = 5
        while retries > 0:
            try:
                logging.info(f"[Broker] Attempting to connect to RabbitMQ at '{self.host}'...")

                # Connect to RabbitMQ
                parameters = pika.ConnectionParameters(host=self.host)
                self.connection = pika.BlockingConnection(parameters)
                self.channel = self.connection.channel()

                # 1. Declare a FANOUT exchange instead of a queue
                self.channel.exchange_declare(
                    exchange='cv_metrics_exchange',
                    exchange_type='fanout'
                )
                logging.info("[Broker] Connected to Fanout Exchange.")
                return
            except pika.exceptions.AMQPConnectionError:
                retries -= 1
                logging.warning(f"[Broker] Connection failed. Retrying in 3 seconds... ({retries} left)")
                time.sleep(3)

        logging.error("[Broker] Could not connect to RabbitMQ. Metrics will not be published.")

    def publish(self, message: dict):
        if not self.connection or self.connection.is_closed:
            return
        try:
            json_payload = json.dumps(message)
            # 2. Publish to the EXCHANGE, not a routing_key
            self.channel.basic_publish(
                exchange='cv_metrics_exchange',
                routing_key='', # Routing key is ignored in fanout
                body=json_payload
            )
        except Exception as e:
            logging.error(f"[Broker] Publish failed: {e}")

    def close(self):
        if self.connection and not self.connection.is_closed:
            self.connection.close()
            logging.info("[Broker] Connection cleanly closed.")