import logging
import multiprocessing as mp
import os
import queue
import signal
import threading
import time
from dataclasses import dataclass
from multiprocessing import shared_memory
from typing import List, Dict

import cv2
import numpy as np

from fake_camera import FakeCamera, CameraError
from message_broker import MessageBrokerClient


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

@dataclass
class PipelineConfig:
    cameras: List[Dict]
    capture_queue_size: int = 60
    reporter_queue_size: int = 200
    max_reconnect_backoff: float = 10.0
    batch_size: int = 4
    broker_topic: str = "cv.pipeline.results"
    broker_host: str = "localhost"


# ---------------------------------------------------------
# Stage 1: Capture
# ---------------------------------------------------------

def camera_worker(cam_cfg: dict, out_queue: mp.Queue, stop_event: mp.Event, config: PipelineConfig):
    cam_id = cam_cfg["camera_id"]
    fps = cam_cfg["fps"]
    backoff = 1.0

    while not stop_event.is_set():
        try:
            logging.info(f"[Capture] Connecting to {cam_id}...")
            cam = FakeCamera(camera_id=cam_id, fps=fps)
            backoff = 1.0

            while not stop_event.is_set():
                frame = cam.read()

                # 1. Allocate Shared Memory
                shm = shared_memory.SharedMemory(create=True, size=frame.nbytes)

                # 2. Map and copy data
                shm_array = np.ndarray(frame.shape, dtype=frame.dtype, buffer=shm.buf)
                shm_array[:] = frame[:]

                payload = {
                    "cam_id": cam_id,
                    "frame_num": cam.frame_count,
                    "timestamp": time.time(),
                    "shm_name": shm.name,
                    "shape": frame.shape,
                    "dtype": str(frame.dtype)
                }

                try:
                    out_queue.put_nowait(payload)
                    from multiprocessing.resource_tracker import unregister
                    unregister(shm._name, "shared_memory")

                except queue.Full:
                    # If queue is full, the producer still owns the memory.
                    shm.close()
                    shm.unlink()
                    logging.warning(f"[Capture] Queue full. Dropped frame {cam.frame_count}. SHM unlinked.")

        except CameraError as e:
            logging.error(f"[Capture] {e}. Reconnecting in {backoff}s...")
            # Use short sleeps so we can interrupt the backoff during shutdown
            for _ in range(int(backoff * 10)):
                if stop_event.is_set(): break
                time.sleep(0.1)
            backoff = min(backoff * 2, config.max_reconnect_backoff)
        except Exception as e:
            logging.error(f"[Capture] Unexpected error: {e}")
            break


def capture_stage(config: PipelineConfig, out_queue: mp.Queue, stop_event: mp.Event):
    """Stage 1: Manages camera threads."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ignore CTRL+C
    logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(processName)-15s | %(levelname)-7s | %(message)s')

    threads = []
    for cam_cfg in config.cameras:
        t = threading.Thread(target=camera_worker, args=(cam_cfg, out_queue, stop_event, config))
        t.start()
        threads.append(t)

    # Wait for the stop signal
    stop_event.wait()

    # Wait for threads to cleanly wrap up their final frames
    for t in threads:
        t.join()
    logging.info("[Capture] All camera threads safely shut down.")


# ---------------------------------------------------------
# Stage 2: Processing (Batched)
# ---------------------------------------------------------

def processing_stage(in_queue: mp.Queue, out_queue: mp.Queue, stop_event: mp.Event, batch_size: int):
    """Stage 2: Batches frames, applies preprocessing, and simulates GPU inference."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ignore CTRL+C
    logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(processName)-15s | %(levelname)-7s | %(message)s')
    logging.info(f"[Processing] Stage started. GPU Batch Size: {batch_size}")

    while not stop_event.is_set() or not in_queue.empty():
        batch = []
        try:
            batch.append(in_queue.get(timeout=0.5))
            while len(batch) < batch_size and not stop_event.is_set():
                try:
                    batch.append(in_queue.get(timeout=0.01))
                except queue.Empty:
                    break
        except queue.Empty:
            continue

        if not batch:
            continue

        start_proc_time = time.time()
        results = []
        shms_to_close = []

        for payload in batch:
            try:
                shm = shared_memory.SharedMemory(name=payload["shm_name"], track=False)
                shms_to_close.append(shm)

                frame = np.ndarray(payload["shape"], dtype=np.dtype(payload["dtype"]), buffer=shm.buf)
                resized = cv2.resize(frame, (640, 480))
                gray = cv2.cvtColor(resized, cv2.COLOR_RGB2GRAY)
                results.append(payload)
            except Exception as e:
                logging.error(f"[Processing] Error preparing frame: {e}")

        # Simulated GPU Batch Inference
        time.sleep(0.1)
        end_proc_time = time.time()

        for payload in results:
            res = {
                "cam_id": payload["cam_id"],
                "frame_num": payload["frame_num"],
                "timestamp": payload["timestamp"],
                "proc_latency": end_proc_time - start_proc_time,
                "total_latency": end_proc_time - payload["timestamp"],
                "batch_size": len(batch)
            }
            try:
                out_queue.put_nowait(res)
            except queue.Full:
                logging.warning("[Processing] Reporter queue full.")

        # Cleanup shared memory for the batch
        for shm in shms_to_close:
            try:
                shm.close()
                shm.unlink()
            except Exception:
                pass

    logging.info("[Processing] Worker shut down safely.")


# ---------------------------------------------------------
# Stage 3: Reporter
# ---------------------------------------------------------

def reporter_stage(in_queue: mp.Queue, stop_event: mp.Event, broker_topic: str, broker_host: str):
    """Stage 3: Logs final results and publishes to RabbitMQ."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(processName)-15s | %(levelname)-7s | %(message)s')
    logging.info("[Reporter] Stage started.")

    # 1. Initialize and connect to RabbitMQ
    broker = MessageBrokerClient(host=broker_host, queue_name=broker_topic)
    broker.connect()

    while not stop_event.is_set() or not in_queue.empty():
        try:
            res = in_queue.get(timeout=0.5)

            # 2. Publish to RabbitMQ
            broker.publish(res)

            logging.info(
                f"[Report] {res['cam_id']} | Frame: {res['frame_num']:04d} | "
                f"Batch: {res.get('batch_size', 1)} | "
                f"Proc Time: {res['proc_latency']:.3f}s | "
                f"Total Latency: {res['total_latency']:.3f}s"
            )
        except queue.Empty:
            continue

    broker.close()
    logging.info("[Reporter] Shut down safely.")

# ---------------------------------------------------------
# Orchestration & Entry Point
# ---------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s.%(msecs)03d | %(processName)-15s | %(levelname)-7s | %(message)s',
        datefmt='%H:%M:%S'
    )

    # Read the host from the environment variable set by Docker Compose
    rabbit_host = os.environ.get("BROKER_HOST", "localhost")

    config = PipelineConfig(
        cameras=[
            {"camera_id": "cam_01", "fps": 15},
            {"camera_id": "cam_02", "fps": 15},
            {"camera_id": "cam_03", "fps": 10},
            {"camera_id": "cam_04", "fps": 10},
        ],
        broker_host=rabbit_host  # <--- Ensure this is passed into config
    )

    capture_to_process_q = mp.Queue(maxsize=config.capture_queue_size)
    process_to_report_q = mp.Queue(maxsize=config.reporter_queue_size)
    stop_event = mp.Event()

    processes = []

    # 1 Capture Process
    processes.append(
        mp.Process(target=capture_stage, args=(config, capture_to_process_q, stop_event), name="Proc-Capture"))

    # 3 Processing Workers
    for i in range(3):
        processes.append(mp.Process(target=processing_stage,
                                    args=(capture_to_process_q, process_to_report_q, stop_event, config.batch_size),
                                    name=f"Proc-Inference-{i + 1}"))

    # 1 reporter process
    processes.append(
        mp.Process(
            target=reporter_stage,
            args=(process_to_report_q, stop_event, config.broker_topic, config.broker_host),
            name="Proc-Reporter"
        )
    )

    def handle_shutdown(signum, frame):
        logging.info("\n[Main] Shutdown signal received. Telling workers to stop...")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    logging.info("[Main] Starting Pipeline...")
    for p in processes:
        p.start()

    # --- THE HEALTH MONITOR LOOP ---
    try:
        # Keep checking processes as long as we haven't been told to shut down
        while not stop_event.is_set():
            for i in range(len(processes)):
                p = processes[i]
                if not p.is_alive():
                    logging.warning(f"[HealthCheck] 🚨 {p.name} died unexpectedly! Restarting...")

                    # Create a brand new process using the exact same target and arguments
                    new_p = mp.Process(target=p._target, args=p._args, name=p.name)
                    new_p.start()
                    processes[i] = new_p  # Replace the dead process in our list

            # Sleep briefly so the Main process doesn't hog the CPU
            time.sleep(2.0)

    except KeyboardInterrupt:
        pass

    # Wait for all processes to gracefully shut down on their own
    for p in processes:
        p.join()

    # The absolute final cleanup phase
    logging.info("[Main] All workers dead. Draining queues to sweep up leftover frames...")

    # Loop with a tiny timeout because q.empty() is notoriously unreliable in mp
    while True:
        try:
            payload = capture_to_process_q.get(timeout=0.1)
            shm = shared_memory.SharedMemory(name=payload["shm_name"], track=False)
            shm.close()
            shm.unlink()
        except queue.Empty:
            break
        except Exception:
            pass

    logging.info("[Main] Pipeline cleanly shut down.")


if __name__ == "__main__":
    main()