import re
import sys
import logging
import subprocess
import multiprocessing
import queue as queue_module

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

TUNNEL_START_TIMEOUT = 20
RSD_PATTERNS = (
    re.compile(r"--rsd (\S+) (\d+)"),
    re.compile(r"^(\S+)\s+(\d+)$"),
)


def parse_rsd_output(output):
    for pattern in RSD_PATTERNS:
        match = pattern.search(output)
        if match:
            return match.group(1), int(match.group(2))
    return None, None


def build_tunnel_error(return_code, output_lines):
    if output_lines:
        recent_output = " | ".join(output_lines[-5:])
        return f"start-tunnel exited before emitting an RSD address (exit code {return_code}). Recent output: {recent_output}"
    return f"start-tunnel exited before emitting an RSD address (exit code {return_code})"

def start_tunnel(queue):
    command = [sys.executable, '-m', 'pymobiledevice3', 'lockdown', 'start-tunnel', '--script-mode']

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        errors='replace'
    )

    logging.info("Tunnel started")

    if process.stdout is None:
        queue.put(("error", "start-tunnel did not provide a readable output stream"))
        return

    output_lines = []

    while True:
        output = process.stdout.readline()
        if output == "" and process.poll() is not None:
            break

        line = output.strip()
        if not line:
            continue

        output_lines.append(line)
        logging.info(line)

        address, port = parse_rsd_output(line)
        if address is not None:
            queue.put(("ok", address, port))
            logging.info(f"RSD Address: {address}, RSD Port: {port}")
            process.wait()
            return

    return_code = process.wait()
    queue.put(("error", build_tunnel_error(return_code, output_lines)))

def tunnel():
    queue = multiprocessing.Queue()
    process = multiprocessing.Process(target=start_tunnel, args=(queue,))
    process.start()
    
    try:
        result = queue.get(timeout=TUNNEL_START_TIMEOUT)
        if result is None:
            raise RuntimeError("failed to establish tunnel connection")
        status, *payload = result
        if status != "ok":
            raise RuntimeError(payload[0] if payload else "未知错误")

        address, port = payload
        
        return process, address, port
    except queue_module.Empty:
        logging.error(
            f"❌ 隧道建立失败: timed out after {TUNNEL_START_TIMEOUT}s waiting for an RSD address. "
            "Ensure the terminal is running as administrator, the device is unlocked and trusted, and run pymobiledevice3 mounter auto-mount first"
        )
    except Exception as e:
        logging.error(f"❌ 隧道建立失败: {e}")
        process.terminate()
        process.join(timeout=2)
        if process.is_alive():
            process.kill()

    if process.is_alive():
        process.terminate()
        process.join(timeout=2)
        if process.is_alive():
            process.kill()

    return None, None, None
