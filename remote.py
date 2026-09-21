import http.client
import json
import os
import random
import socket
import ssl
import time
import urllib.parse

TIMEOUT = 300
UA = "InstaladorMods/1.0"
CHUNK = 262144
RETRIABLE = (502, 503, 504)
REINTENTOS = 5
COLA_MAX_INTENTOS = 40
COLA_MAX_SEGUNDOS = 180.0


class RemoteError(Exception):
    pass


class _HttpError(RemoteError):
    def __init__(self, status, nombre, retry_after=0.0):
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"HTTP {status} al descargar {nombre}")


def _direcciones(host, puerto):
    try:
        infos = socket.getaddrinfo(host, puerto, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise RemoteError(f"No se pudo resolver el origen: {host} ({e})") from e
    infos.sort(key=lambda i: 0 if ":" not in i[4][0] else 1)
    return infos


class _ConexionHTTPS(http.client.HTTPSConnection):
    def connect(self):
        contexto = self._context
        if contexto is None:
            contexto = ssl.create_default_context()
        ultimo = None
        for _, __, __, __, saddr in _direcciones(self.host, self.port):
            try:
                s = socket.create_connection(saddr, self.timeout, self.source_address)
                self.sock = contexto.wrap_socket(
                    s, server_hostname=getattr(self, "_server_hostname", None) or self.host
                )
                return
            except OSError as e:
                ultimo = e
                try:
                    s.close()
                except Exception:
                    pass
        raise ultimo or OSError("No se pudo conectar al origen.")


class _ConexionHTTP(http.client.HTTPConnection):
    def connect(self):
        ultimo = None
        for _, __, __, __, saddr in _direcciones(self.host, self.port):
            try:
                self.sock = socket.create_connection(saddr, self.timeout, self.source_address)
                return
            except OSError as e:
                ultimo = e
                try:
                    self.sock.close()
                except Exception:
                    pass
        raise ultimo or OSError("No se pudo conectar al origen.")


def _datos(url):
    u = urllib.parse.urlsplit((url or "").strip().rstrip("/"))
    if u.scheme not in ("http", "https") or not u.hostname:
        raise RemoteError("URL del origen no válida.")
    return u


def _nueva_conexion(url):
    u = _datos(url)
    if u.scheme == "https":
        return _ConexionHTTPS(u.hostname, u.port or 443, timeout=TIMEOUT)
    return _ConexionHTTP(u.hostname, u.port or 80, timeout=TIMEOUT)


def _ruta_base(url):
    return (_datos(url).path or "").rstrip("/")


def fetch_manifest(url):
    ultimo = None
    for intento in range(REINTENTOS):
        conn = _nueva_conexion(url)
        try:
            conn.request(
                "GET",
                _ruta_base(url) + "/manifest",
                headers={"User-Agent": UA, "Accept-Encoding": "identity", "Connection": "close"},
            )
            r = conn.getresponse()
            if r.status == 200:
                payload = json.loads(r.read().decode("utf-8"))
                return [m for m in payload.get("mods", []) if m.get("name")]
            if r.status in RETRIABLE:
                ultimo = f"HTTP {r.status} al consultar el origen.\nURL usada: {url}"
                _espera_reintento(intento)
                continue
            raise RemoteError(f"HTTP {r.status} al consultar el origen.\nURL usada: {url}")
        except RemoteError:
            raise
        except Exception as e:
            ultimo = f"No se pudo consultar el origen: {e}\nURL usada: {url}"
            _espera_reintento(intento)
        finally:
            try:
                conn.close()
            except Exception:
                pass
    raise RemoteError(ultimo or "No se pudo consultar el origen.")


def compare(locales, manifest):
    rem = {m["name"]: m for m in manifest}
    descargar = sorted(n for n in rem if n not in locales)
    actualizar = sorted(n for n, m in rem.items() if n in locales and m["size"] != locales[n])
    iguales = sorted(n for n, m in rem.items() if n in locales and m["size"] == locales[n])
    eliminar = sorted(n for n in locales if n not in rem and n.lower().endswith(".jar"))
    return descargar, actualizar, eliminar, iguales


def _bajar_por_conn(conn, base, nombre, destino, progreso=None):
    conn.request(
        "GET",
        base + "/mods/" + urllib.parse.quote(nombre),
        headers={"User-Agent": UA, "Accept-Encoding": "identity", "Connection": "close"},
    )
    r = conn.getresponse()
    if r.status != 200:
        retry_after = 0.0
        try:
            retry_after = float(r.getheader("Retry-After") or 0) or 0.0
        except (TypeError, ValueError):
            retry_after = 0.0
        raise _HttpError(r.status, nombre, retry_after=retry_after)
    total = 0
    try:
        total = int(r.getheader("Content-Length") or 0)
    except (TypeError, ValueError):
        total = 0
    leido = 0
    with open(destino, "wb") as f:
        while True:
            data = r.read(CHUNK)
            if not data:
                break
            f.write(data)
            leido += len(data)
            if progreso:
                progreso(leido, total)


def _download(url, nombre, destino, progreso=None):
    conn = _nueva_conexion(url)
    try:
        _bajar_por_conn(conn, _ruta_base(url), nombre, destino, progreso)
    finally:
        conn.close()


def _espera_reintento(intento):
    time.sleep(min(1.5 * 2 ** (intento - 1), 12))


def _espera_cola(retry_after):
    base = max(1.0, float(retry_after or 0) or 1.0)
    if base > 30:
        base = 30
    return base * (0.7 + 0.6 * random.random())


def _puede_seguir_cola(inicio):
    return time.monotonic() - inicio < COLA_MAX_SEGUNDOS


def _bajar_con_reintentos(url, carpeta, nombre, progreso=None, cola_cb=None):
    parcial = os.path.join(carpeta, nombre + ".instalador.part")
    inicio_cola = time.monotonic()
    try:
        for intento in range(1, COLA_MAX_INTENTOS + 1):
            try:
                _download(url, nombre, parcial, progreso)
                os.replace(parcial, os.path.join(carpeta, nombre))
                return
            except _HttpError as e:
                if e.status == 503:
                    if not _puede_seguir_cola(inicio_cola):
                        raise RemoteError(
                            "El servidor está ocupado (cola llena). "
                            "Vuelve a intentarlo en unos minutos."
                        ) from e
                    if cola_cb:
                        cola_cb(nombre)
                    time.sleep(_espera_cola(e.retry_after))
                    continue
                if e.status not in RETRIABLE or intento >= REINTENTOS:
                    raise
            except (http.client.RemoteDisconnected, http.client.BadStatusLine, ConnectionError, OSError) as e:
                if intento >= REINTENTOS:
                    raise RemoteError(f"No se pudo descargar {nombre}: la conexión falló ({e}).")
            _espera_reintento(intento)
        raise RemoteError(f"No se pudo descargar {nombre}.")
    finally:
        try:
            os.remove(parcial)
        except OSError:
            pass


def descargar_uno(url, carpeta, nombre, progreso=None, cola_cb=None):
    os.makedirs(carpeta, exist_ok=True)
    _bajar_con_reintentos(url, carpeta, nombre, progreso, cola_cb)


def ordenar_por_tamano(manifest, nombres):
    sizes = {m["name"]: int(m.get("size") or 0) for m in manifest}
    pendientes = sorted(n for n in nombres if n in sizes)
    sin_tam = sorted(n for n in nombres if n not in sizes)
    return sorted(pendientes, key=lambda n: sizes[n]) + sin_tam


def sync_from_remote(url, carpeta, descargar, actualizar, eliminar=None, progress_cb=None, cola_cb=None):
    eliminar = [n for n in (eliminar or [])]
    os.makedirs(carpeta, exist_ok=True)
    total = len(descargar) + len(actualizar) + len(eliminar)
    hecho = 0
    listos = []
    actualizados = []
    eliminados = []

    for nombre in eliminar:
        ruta = os.path.join(carpeta, nombre)
        try:
            if os.path.isfile(ruta):
                os.remove(ruta)
        except OSError as e:
            raise RemoteError(f"No se pudo eliminar {nombre}: {e}")
        eliminados.append(nombre)
        hecho += 1
        if progress_cb:
            progress_cb(hecho, total, nombre)

    def bajar(nombre):
        _bajar_con_reintentos(url, carpeta, nombre, cola_cb=cola_cb)

    for nombre in descargar:
        bajar(nombre)
        listos.append(nombre)
        hecho += 1
        if progress_cb:
            progress_cb(hecho, total, nombre)
    for nombre in actualizar:
        bajar(nombre)
        actualizados.append(nombre)
        hecho += 1
        if progress_cb:
            progress_cb(hecho, total, nombre)
    return listos, actualizados, eliminados