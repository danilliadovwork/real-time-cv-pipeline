# Real-Time Computer Vision Pipeline & Monitoring Dashboard

A highly resilient, multi-process Computer Vision (CV) pipeline with an asynchronous monitoring dashboard. This project is designed to handle multiple camera streams, perform batched simulated GPU inference, and stream real-time metrics via RabbitMQ to a modern web interface.

## 🏗️ Architecture Overview

The system is containerized using Docker Compose and consists of three main services:

1. **CV Pipeline (`cv_pipeline`)**: A robust, multi-process Python backend that captures frames, processes them in batches using shared memory, and publishes metrics.
2. **Message Broker (`rabbitmq_broker`)**: A RabbitMQ instance that acts as the asynchronous communication layer between the pipeline and the dashboard.
3. **Dashboard (`cv_dashboard`)**: A modern web application built with [Reflex](https://reflex.dev/) that subscribes to the RabbitMQ stream and visualizes real-time camera metrics.

## ✨ Core Features

* **Multi-Process Backend:** Completely isolates the Capture, Processing, and Reporter stages to maximize CPU utilization and prevent blocking.
* **Zero-Copy IPC:** Utilizes Python's `multiprocessing.shared_memory` alongside `Queues` to pass heavy image payloads between processes without expensive memory duplication.
* **Dynamic Batching:** Groups frames from multiple cameras into batches to simulate optimized GPU inference workloads.
* **Resilience & Health Monitoring:** Includes exponential backoff for camera disconnects and an active main-loop health checker that automatically respawns dead worker processes.
* **Graceful Shutdown & Backpressure:** Captures `SIGTERM`/`SIGINT` to safely release shared memory blocks, drain queues, and drop frames gracefully if processing falls behind.
* **Live Dashboard:** Displays real-time frame numbers, batch sizes, processing latency, and total end-to-end latency for each active camera.

## 🚀 Getting Started

### Prerequisites
* [Docker](https://docs.docker.com/get-docker/) and Docker Compose installed on your host machine.

### Running the Application

1. Clone the repository and navigate to the root directory containing the `docker-compose.yml` file.
2. Build and start the containers:
   ```bash
   docker compose up --build
   ```
3.To run it in the background (detached mode), use:
```
    docker compose up -d --build
```

## Accessing the Services
Once the containers are successfully running, you can access the following endpoints:

CV Dashboard UI: http://localhost:3000

RabbitMQ Management Console: http://localhost:15672 (Credentials: guest / guest)

## 🛠️ Service Configuration Details
1. Pipeline (./video_processor)
Mounts the local ./video_processor directory to /app inside the container for rapid development.

Crucial Detail: Allocates 2gb of shared memory (shm_size) to prevent Docker's default 64MB limit from crashing the application during heavy multi-camera IPC transfers.

Waits for the RabbitMQ health check to pass before starting.

2. RabbitMQ (rabbitmq:3-management)
Exposes standard AMQP port 5672 and management port 15672.

Configured with a strict health check to ensure the broker is fully responsive before dependent services boot up.

3. Dashboard (./cv_dashboard)
Mounts the local ./cv_dashboard directory to /app.

Connects to the broker using the internal Docker DNS via the BROKER_URL=amqp://guest:guest@rabbitmq/ environment variable.

## 🛑 Stopping the Application
To gracefully stop the pipeline and release all shared memory resources, use:
```
docker compose down
```


## 🧠 System Design & Evaluation Criteria

This pipeline was designed with a focus on real-time performance, fault tolerance, and clean architectural boundaries. Below is a breakdown of how the system addresses core engineering criteria.

### 1. Architecture & Separation of Concerns
The system utilizes a strict multi-process architecture divided into three distinct, decoupled stages:
* **Capture Stage (1 Process, Multi-threaded):** Dedicated purely to I/O bound tasks. It reads frames from multiple cameras concurrently using threads, isolating connection logic from heavy computation.
* **Processing Stage (N Processes):** A pool of worker processes that handle CPU-bound tasks (simulated GPU inference). They consume from a shared queue, allowing dynamic scaling of workers based on load.
* **Reporter Stage (1 Process):** Dedicated to network I/O. It aggregates results and publishes them to the RabbitMQ broker, ensuring the processing workers are never blocked by network latency.

### 2. IPC Design (Inter-Process Communication)
Passing raw, uncompressed 1080p frames between processes via standard pipes or queues introduces massive serialization overhead. This pipeline uses a **Hybrid IPC Approach**:
* **Shared Memory (`multiprocessing.shared_memory`):** Used for the heavy payload. Frame data is written to a shared memory block (zero-copy), avoiding expensive pickling/unpickling.
* **Queues (`multiprocessing.Queue`):** Used strictly for lightweight metadata routing. The queue passes a dictionary containing the `shm_name` (a pointer), frame number, and shape.
* **RabbitMQ:** Used for external Inter-Service Communication, bridging the backend pipeline to the asynchronous web dashboard.

### 3. Reliability & Lifecycle Management
The pipeline is built to self-heal and shut down cleanly:
* **Disconnections:** Handled at the thread level. If a camera raises a `CameraError`, only that specific thread enters an exponential backoff loop to reconnect. Other cameras continue streaming seamlessly.
* **Process Health Checks:** The main orchestrator loop continuously monitors worker processes (`p.is_alive()`). If a worker dies (e.g., due to a segfault or OOM), the main process automatically respawns it with its original arguments.
* **Graceful Shutdown:** On `SIGINT` or `SIGTERM`, a global `mp.Event` is set. Workers finish their current batch, release their resources, and exit. The main process then drains the queues and unlinks any orphaned shared memory to prevent leaks.

### 4. Resource Management & Backpressure
Memory lifecycle and ownership are strictly enforced to prevent unbound memory growth:
* **Ownership Transfer:** The Capture stage allocates the shared memory block. Ownership is immediately transferred to the Processing stage via the queue. Once the Processing worker finishes the batch, it is responsible for calling `shm.unlink()`.
* **Backpressure Handling:** If the Processing workers cannot keep up, the `capture_to_process_q` fills up. The Capture stage uses `put_nowait()`; upon catching `queue.Full`, it immediately unlinks the shared memory and drops the frame. This ensures the system degrades gracefully by dropping frames rather than crashing from Out-Of-Memory (OOM) errors.

### 5. Code Quality
* **Type Hinting & Dataclasses:** Extensive use of Python typing and `dataclasses` (e.g., `PipelineConfig`) ensures clear contracts between components and makes configuration highly readable.
* **Maintainability:** Standard library modules (`logging`, `signal`, `multiprocessing`) are favored over heavy third-party dependencies where possible. The code avoids over-engineering by using straightforward procedural flow within the worker functions while maintaining strict boundaries between stages.

### 6. Trade-off Awareness
* **Dropped Frames vs. Blocking:** *Trade-off:* We drop frames when the queue is full instead of blocking the capture thread. *Reasoning:* In real-time CV, processing old, delayed frames is usually useless. It is better to drop frames and process the most recent data than to introduce compounding latency.
* **Lack of Strict Frame Synchronization:** *Trade-off:* Frames from different cameras are processed as they arrive (FIFO), without waiting to group them by exact timestamp. *Reasoning:* Strict synchronization introduces artificial latency bottlenecks (waiting for the slowest camera). For independent camera streams, async batching maximizes GPU utilization.
* **Threads vs. Processes in Capture:** *Trade-off:* Cameras are read via threads inside a single process, rather than a process per camera. *Reasoning:* Reading `cv2.VideoCapture` is largely an I/O bound operation that releases the GIL. Threads use less overhead than processes, keeping the architecture lighter while still achieving concurrency.