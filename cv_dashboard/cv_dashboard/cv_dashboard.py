import json
import os  # <-- Add this
import aio_pika
import reflex as rx


class DashboardState(rx.State):
    """The app state linking the backend data to the frontend UI."""
    # Maps camera ID to its latest metrics
    metrics: dict[str, dict[str, str]] = {}
    is_listening: bool = False



    @rx.event(background=True)
    async def listen_to_broker(self):
        """Background task that consumes RabbitMQ messages asynchronously."""
        async with self:
            if self.is_listening:
                return
            self.is_listening = True

        try:
            # Connect to the RabbitMQ container
            broker_url = os.getenv("BROKER_URL", "amqp://guest:guest@localhost/")
            connection = await aio_pika.connect_robust(broker_url)

            async with connection:
                channel = await connection.channel()
                # Must match the queue name from the CV pipeline exactly
                queue = await channel.declare_queue("cv.pipeline.results", durable=True)
                
                async with queue.iterator() as queue_iter:
                    async for message in queue_iter:
                        async with message.process():
                            # Parse the JSON payload from the CV pipeline
                            data = json.loads(message.body.decode())
                            cam_id = data["cam_id"]
                            
                            # Format numbers nicely for the UI
                            formatted_data = {
                                "frame_num": str(data["frame_num"]),
                                "batch_size": str(data.get("batch_size", 1)),
                                "proc_latency": f"{data['proc_latency']:.3f}s",
                                "total_latency": f"{data['total_latency']:.3f}s",
                            }
                            
                            # Update the UI state
                            async with self:
                                # Reassigning the dictionary triggers a UI refresh
                                self.metrics[cam_id] = formatted_data
                                
        except Exception as e:
            print(f"RabbitMQ Connection Error: {e}")
            async with self:
                self.is_listening = False


# ---------------------------------------------------------
# UI Components
# ---------------------------------------------------------

def metric_stat(label: str, value: str):
    return rx.card(
        rx.vstack(
            rx.text(label, size="2", color_scheme="gray"),
            rx.heading(value, size="6"),
            spacing="1",
        ),
        width="100%",
    )
def camera_card(item: list) -> rx.Component:
    """Renders a UI card for a single camera."""
    # Reflex passes dictionary items as a list: [key, value]
    cam_id = item[0]
    data = item[1]
    
    return rx.card(
        rx.vstack(
            rx.heading(cam_id, size="6", color_scheme="blue"),
            rx.divider(),
            rx.grid(
                metric_stat("Frame Number", data["frame_num"]),
                metric_stat("Batch Size", data["batch_size"]),
                metric_stat("Processing Time", data["proc_latency"]),
                metric_stat("Total Latency", data["total_latency"]),
                columns="2",
                spacing="4",
                width="100%"
            ),
        ),
        width="100%",
        box_shadow="md",
        border_radius="lg"
    )

def index() -> rx.Component:
    """The main page layout."""
    return rx.container(
        rx.vstack(
            rx.heading("Computer Vision Pipeline Monitor", size="8", margin_bottom="1em"),
            
            # Start button
            rx.button(
                rx.cond(
                    DashboardState.is_listening,
                    "Listening to Stream (Live)",
                    "Connect to RabbitMQ Stream"
                ),
                on_click=DashboardState.listen_to_broker,
                is_disabled=DashboardState.is_listening,
                color_scheme=rx.cond(DashboardState.is_listening, "green", "blue"),
                size="3",
                margin_bottom="2em"
            ),
            
            # Responsive grid displaying the camera cards
            rx.grid(
                rx.foreach(
                    DashboardState.metrics,
                    camera_card
                ),
                columns=rx.breakpoints(initial="1", sm="2", lg="2"), # 1 column on mobile, 2 on desktop
                spacing="6",
                width="100%",
            ),
            align_items="center",
            padding_top="4em",
            padding_bottom="4em",
        ),
        max_width="800px"
    )

# App Initialization
app = rx.App(theme=rx.theme(appearance="dark", accent_color="blue"))
app.add_page(index, title="CV Dashboard")