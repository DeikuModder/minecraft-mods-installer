import http.client
import json
import os
import time
import urllib.parse

TIMEOUT = 300
UA = "InstaladorMods/1.0"
CHUNK = 262144
RETRIABLE = (502, 503, 504)
REINTENTOS = 3


class RemoteError(Exception):
    pass


class _HttpError(RemoteError):
    def __init__(self, status, nombre):
        self.status = status
        super().__init__(f"HTTP {status} al descargar {nombre}")


def _datos(url):
    u = urllib.parse.urlsplit((url or "").strip().rstrip("/"))
    if u.scheme not in ("http", "https") or not u.hostname:
        raise RemoteError("URL del origen no válida.")
    return u


def _nueva_conexion(url):
    u = _datos(url)
    if u.scheme == "https":
        return http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=TIMEOUT)
    return http.client.HTTPConnection(u.hostname, u.port or 80, timeout=TIMEOUT)


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
                headers={"User-Agent": UA, "Accept-Encoding": "identity"},
            )
            r = conn.getresponse()
            if r.status == 200:
                payload = json.loads(r.read().decode("utf-8"))
                return [m for m in payload.get("mods", []) if m.get("name")]
            if r.status in RETRIABLE:
                ultimo = f"HTTP {r.status} al consultar el origen."
                time.sleep(1.5)
                continue
            raise RemoteError(f"HTTP {r.status} al consultar el origen.")
        except RemoteError:
            raise
        except Exception as e:
            ultimo = f"No se pudo consultar el origen: {e}"
            time.sleep(1.5)
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
    return descargar, actualizar, iguales


def _bajar_por_conn(conn, base, nombre, destino):
    conn.request(
        "GET",
        base + "/mods/" + urllib.parse.quote(nombre),
        headers={"User-Agent": UA, "Accept-Encoding": "identity"},
    )
    r = conn.getresponse()
    if r.status != 200:
        raise _HttpError(r.status, nombre)
    with open(destino, "wb") as f:
        while True:
            data = r.read(CHUNK)
            if not data:
                break
            f.write(data)


def _download(url, nombre, destino):
    conn = _nueva_conexion(url)
    try:
        _bajar_por_conn(conn, _ruta_base(url), nombre, destino)
    finally:
        conn.close()


def sync_from_remote(url, carpeta, descargar, actualizar, progress_cb=None):
    os.makedirs(carpeta, exist_ok=True)
    base = _ruta_base(url)
    conn = _nueva_conexion(url)
    total = len(descargar) + len(actualizar)
    hecho = 0
    listos = []
    actualizados = []

    def bajar(nombre):
        nonlocal conn
        parcial = os.path.join(carpeta, nombre + ".instalador.part")
        intentos = 0
        while True:
            intentos += 1
            try:
                _bajar_por_conn(conn, base, nombre, parcial)
                os.replace(parcial, os.path.join(carpeta, nombre))
                return
            except _HttpError as e:
                if e.status in RETRIABLE and intentos < REINTENTOS:
                    time.sleep(1.5)
                    try:
                        conn.close()
                    except Exception:
                        pass
                    conn = _nueva_conexion(url)
                    continue
                raise
            except RemoteError:
                raise
            except (http.client.RemoteDisconnected, http.client.BadStatusLine, ConnectionError, OSError):
                if intentos >= REINTENTOS:
                    raise RemoteError(f"No se pudo descargar {nombre}: la conexión falló.")
                time.sleep(1.0)
                try:
                    conn.close()
                except Exception:
                    pass
                conn = _nueva_conexion(url)
            finally:
                try:
                    os.remove(parcial)
                except OSError:
                    pass

    try:
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
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return listos, actualizados