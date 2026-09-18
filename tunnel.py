import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

import config

DOWNLOAD_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
CFD = os.path.join(os.path.expanduser("~"), ".cloudflared")


def _candidatos():
    res = []
    visto = set()

    def add(p):
        if p and p not in visto:
            visto.add(p)
            if os.path.isfile(p):
                res.append(p)

    add(shutil.which("cloudflared"))
    add(r"C:\Program Files (x86)\cloudflared\cloudflared.exe")
    add(r"C:\Program Files\cloudflared\cloudflared.exe")
    add(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "cloudflared.exe"))
    add(os.path.join(config.APP_DIR, "bin", "cloudflared.exe"))
    return res


def find_cloudflared():
    cand = _candidatos()
    return cand[0] if cand else None


def ensure_cloudflared(progress_cb=None):
    found = find_cloudflared()
    if found:
        return found
    dst = os.path.join(config.APP_DIR, "bin", "cloudflared.exe")
    tmp = dst + ".part"
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    req = urllib.request.Request(DOWNLOAD_URL, headers={"User-Agent": "InstaladorMods"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        hecho = 0
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)
            hecho += len(chunk)
            if progress_cb and total:
                progress_cb(hecho / total)
    os.replace(tmp, dst)
    return dst


def is_logged_in():
    return os.path.isfile(os.path.join(CFD, "cert.pem"))


def login(exe, on_url=None, timeout=300):
    if is_logged_in():
        return True
    p = subprocess.Popen(
        [exe, "tunnel", "login"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    def leer():
        try:
            for line in p.stdout:
                if on_url and "dash.cloudflare.com/argotunnel" in line:
                    m = re.search(r"(https://dash\.cloudflare\.com/argotunnel\S+)", line)
                    if m:
                        on_url(m.group(1))
                if re.search(r"select which|enter\s+a?\s*number|multiple zones", line, re.I):
                    try:
                        p.stdin.write("1\n")
                        p.stdin.flush()
                    except Exception:
                        pass
        except Exception:
            pass

    threading.Thread(target=leer, daemon=True).start()
    t0 = time.time()
    while time.time() - t0 < timeout:
        if is_logged_in():
            break
        if p.poll() is not None:
            break
        time.sleep(0.5)
    try:
        p.kill()
    except Exception:
        pass
    return is_logged_in()


def _run(exe, args, timeout=120):
    try:
        return subprocess.run(
            [exe] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"El comando cloudflared tardó demasiado: {' '.join(args)}") from e


def ensure_tunnel(exe, nombre):
    p = _run(exe, ["tunnel", "list"])
    m = re.search(r"(?m)^\s*([0-9a-f-]{36})\s+" + re.escape(nombre) + r"\s", p.stdout)
    if p.returncode == 0 and m:
        return m.group(1)
    p2 = _run(exe, ["tunnel", "create", nombre])
    m2 = re.search(r"id\s+([0-9a-f-]{36})", p2.stdout, re.I)
    if p2.returncode == 0 and m2:
        return m2.group(1)
    return None


def route(exe, nombre, hostname):
    p = _run(exe, ["tunnel", "route", "dns", nombre, hostname])
    salida = (p.stdout + p.stderr).strip()
    ok = p.returncode == 0 or "already" in salida.lower()
    return ok, salida


def preparar(exe, tunnel_name, hostname, aviso=None):
    if not is_logged_in():
        if aviso:
            aviso("Abriendo el navegador para iniciar sesión en Cloudflare...")
        if not login(exe):
            raise RuntimeError("No se completó el inicio de sesión de Cloudflare.")
    if aviso:
        aviso("Verificando o creando el túnel...")
    uuid = ensure_tunnel(exe, tunnel_name)
    if not uuid:
        raise RuntimeError("No se pudo crear el túnel de Cloudflare.")
    if hostname:
        if aviso:
            aviso(f"Asociando el hostname {hostname}...")
        ok, salida = route(exe, tunnel_name, hostname)
        if not ok:
            raise RuntimeError("No se pudo asociar el hostname:\n" + (salida or "error desconocido"))
        if aviso:
            aviso("Hostname asociado correctamente.")
    return uuid


def write_tunnel_config(archivo_yml, uuid, hostname, port):
    cred = os.path.join(CFD, uuid + ".json").replace("\\", "/")
    yml = (
        "tunnel: " + uuid + "\n"
        "credentials-file: " + cred + "\n"
        "ingress:\n"
        "  - hostname: " + hostname + "\n"
        "    service: " + "http://127.0.0.1:" + str(port) + "\n"
        "  - service: http_status:404\n"
    )
    os.makedirs(os.path.dirname(archivo_yml), exist_ok=True)
    with open(archivo_yml, "w", encoding="utf-8") as f:
        f.write(yml)


def _pid_es_cloudflared(pid):
    try:
        sal = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return "cloudflared" in sal.stdout.lower()
    except Exception:
        return False


def _matar_pid(pid):
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F", "/T"],
            capture_output=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        pass


def limpiar_anteriores():
    cfg = config.load()
    pid = cfg.get("tunnel_pid")
    if pid and _pid_es_cloudflared(pid):
        _matar_pid(pid)
        config.save({"tunnel_pid": None})


def registrar(pid):
    config.save({"tunnel_pid": pid})


def limpiar_registro(pid=None):
    cfg = config.load()
    if pid is None or cfg.get("tunnel_pid") == pid:
        config.save({"tunnel_pid": None})


def start_tunnel(exe, nombre, yml_path, log_path):
    limpiar_anteriores()
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    log = open(log_path, "a" if os.path.exists(log_path) else "w", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(
            [exe, "--no-autoupdate", "--config", yml_path, "tunnel", "run", nombre],
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        log.close()
        raise
    registrar(proc.pid)
    return proc


_READY = re.compile(r"registered tunnel connection|connection\s+\d+\s+established", re.I)


def wait_connection(log_path, since=0, timeout=120, proc=None):
    t0 = time.time()
    last = since
    while time.time() - t0 < timeout:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(last)
                data = f.read()
                last = f.tell()
        except OSError:
            data = ""
        if _READY.search(data):
            return True
        time.sleep(1)
    return False


def stop(proc):
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    limpiar_registro(proc.pid if proc is not None else None)