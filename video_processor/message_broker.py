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

                # Declare the queue (creates it if it doesn't exist)
                self.channel.queue_declare(queue=self.queue_name, durable=True)

                logging.info(f"[Broker] Successfully connected! Publishing to '{self.queue_name}'.")
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
            # Serialize the dictionary to a JSON string
            json_payload = json.dumps(message)

            # Publish to RabbitMQ
            self.channel.basic_publish(
                exchange='',
                routing_key=self.queue_name,
                body=json_payload,
                properties=pika.BasicProperties(
                    delivery_mode=pika.spec.PERSISTENT_DELIVERY_MODE  # Make messages persistent
                )
            )
        except Exception as e:
            logging.error(f"[Broker] Failed to publish message: {e}")

    def close(self):
        if self.connection and not self.connection.is_closed:
            self.connection.close()
            logging.info("[Broker] Connection cleanly closed.")