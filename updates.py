import hashlib
import json
import os
import shutil
import zipfile

import urllib.request

import config
import version

REPO = "DeikuModder/minecraft-mods-installer"
UA = "InstaladorMods/1.0"
TMP = 30


class UpdateError(Exception):
    pass


def _parsear_release(payload):
    nombre = (payload.get("tag_name") or "").lstrip("v")
    if not nombre:
        return None
    body = payload.get("body") or ""
    sha256 = ""
    for linea in body.splitlines():
        if linea.strip().lower().startswith("sha256:"):
            sha256 = linea.split(":", 1)[1].strip().lower()
            break
    url = ""
    for a in payload.get("assets") or []:
        if (a.get("name") or "").lower() == "instaladormods.zip":
            url = a.get("browser_download_url") or ""
            break
    if not url:
        for a in payload.get("assets") or []:
            url = a.get("browser_download_url") or ""
            if url:
                break
    if not url:
        return None
    return {"version": nombre, "sha256": sha256 or None, "url": url}


def version_remota():
    repo = (config.load().get("update_repo") or REPO or "").strip().rstrip("/")
    if not repo:
        return None
    url = "https://api.github.com/repos/" + repo + "/releases/latest"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=TMP) as r:
        payload = json.loads(r.read().decode("utf-8"))
    return _parsear_release(payload)


def _parse(v):
    partes = []
    for p in (v or "").strip().lstrip("v").split("."):
        digitos = ""
        for ch in p:
            if ch.isdigit():
                digitos += ch
            elif digitos:
                break
        partes.append(int(digitos) if digitos else 0)
    while len(partes) < 3:
        partes.append(0)
    return tuple(partes[:3])


def comparar_semver(a, b):
    pa, pb = _parse(a), _parse(b)
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0


def descargar(url, destino, sha256=None, progreso=None):
    tmp = destino + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TMP) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        desc = 0
        while True:
            data = r.read(1 << 16)
            if not data:
                break
            f.write(data)
            desc += len(data)
            if progreso and total:
                progreso(desc, total)
    if sha256:
        h = hashlib.sha256()
        with open(tmp, "rb") as f:
            while True:
                data = f.read(1 << 20)
                if not data:
                    break
                h.update(data)
        if h.hexdigest().lower() != sha256.lower():
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise UpdateError("La descarga no superó la verificación de integridad (SHA-256).")
    os.replace(tmp, destino)


def extraer(zip_ruta, destino):
    with zipfile.ZipFile(zip_ruta) as z:
        z.extractall(destino)


def ubicar_exe(raiz):
    for ruta, _dirs, archivos in os.walk(raiz):
        if "InstaladorMods.exe" in archivos:
            return ruta
    raise UpdateError("El instalador descargado no contiene InstaladorMods.exe.")


def aplicar_upgrade(nuevo, destino):
    if not os.path.isfile(os.path.join(nuevo, "InstaladorMods.exe")):
        raise UpdateError("La nueva versión no contiene InstaladorMods.exe.")
    if not os.path.isdir(destino):
        raise UpdateError("No se encontró la instalación actual.")
    backup = destino + ".backup"
    shutil.rmtree(backup, ignore_errors=True)
    try:
        shutil.move(destino, backup)
    except OSError as e:
        raise UpdateError(
            "No se pudo sustituir la instalación actual. "
            "Cierra el programa y el antivirus y vuelve a intentarlo."
        ) from e
    try:
        shutil.move(nuevo, destino)
    except Exception:
        try:
            if not os.path.isdir(destino) and os.path.isdir(backup):
                shutil.move(backup, destino)
        except Exception:
            pass
        raise
    if not os.path.isfile(os.path.join(destino, "InstaladorMods.exe")):
        try:
            shutil.rmtree(destino, ignore_errors=True)
            if os.path.isdir(backup) and not os.path.isdir(destino):
                shutil.move(backup, destino)
        except Exception:
            pass
        raise UpdateError("La actualización no se pudo aplicar correctamente.")
    shutil.rmtree(backup, ignore_errors=True)
    return os.path.join(destino, "InstaladorMods.exe")