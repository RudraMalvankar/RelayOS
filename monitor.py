import os
import subprocess
import threading
import time

peak_cpu = 0.0
peak_mem = 0.0
is_running = True
cpu_history = []


def monitor():
    global peak_cpu, peak_mem, cpu_history
    while is_running:
        try:
            out = subprocess.check_output(["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}},{{.MemUsage}}"])
            out = out.decode("utf-8").strip()
            if not out:
                time.sleep(0.2)
                continue
            for line in out.splitlines():
                if "," not in line:
                    continue
                cpu_str, mem_str = line.split(",", 1)
                cpu = float(cpu_str.replace("%", "").strip())
                mem_used = mem_str.split("/")[0].strip()
                if "MiB" in mem_used or "MB" in mem_used:
                    memory = float(mem_used.replace("MiB", "").replace("MB", "").strip())
                elif "GiB" in mem_used or "GB" in mem_used:
                    memory = float(mem_used.replace("GiB", "").replace("GB", "").strip()) * 1024
                else:
                    memory = 0.0
                peak_cpu = max(peak_cpu, cpu)
                peak_mem = max(peak_mem, memory)
                cpu_history.append((time.time(), cpu))
        except Exception:
            pass
        time.sleep(0.2)


t = threading.Thread(target=monitor)
t.start()

print("Running pipeline and tracking peak resources...")
docker_env = os.environ.copy()
docker_env["FIREWORKS_API_KEY"] = os.environ.get("FIREWORKS_API_KEY", "")
subprocess.run(
    'docker run --name RelayOS_bench --rm --cpus="2" --memory="4g" '
    '-e FIREWORKS_API_KEY '
    '-v "C:/Users/mypc/Desktop/amd:/app" '
    '-v "C:/Users/mypc/Desktop/amd/input_samples:/input" '
    '-v "C:/Users/mypc/Desktop/amd/output:/output" '
    'ghcr.io/rudramalvankar/RelayOS:latest python main.py 2>&1',
    shell=True,
    env=docker_env,
)

is_running = False
t.join()

print("\n--- PERFORMANCE METRICS ---")
print(f"Peak RAM Usage: {peak_mem:.2f} MiB")
if cpu_history:
    avg_cpu = sum(cpu for _, cpu in cpu_history) / len(cpu_history)
    print(f"Average CPU Usage: {avg_cpu:.2f}%")
    max_5s = 0.0
    exceeded_2vcpus_for_5s = False
    for start, _ in cpu_history:
        window = [cpu for timestamp, cpu in cpu_history if start <= timestamp <= start + 5.0]
        if window:
            avg_5s = sum(window) / len(window)
            max_5s = max(max_5s, avg_5s)
            exceeded_2vcpus_for_5s = exceeded_2vcpus_for_5s or avg_5s > 200.0
    print(f"Maximum sustained CPU (5s rolling): {max_5s:.2f}%")
    print(f"Exceeded 2-vCPU (200%) for 5 consecutive seconds: {exceeded_2vcpus_for_5s}")
else:
    print("No CPU data collected.")
