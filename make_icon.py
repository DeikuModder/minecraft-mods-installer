import struct
import zlib
import os

DESTINO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")
FONDO_A = (41, 43, 63, 255)
FONDO_B = (27, 29, 43, 255)
VERDE = (50, 215, 75, 255)
VERDE_B = (80, 235, 105, 255)


def _fuera_de_esquina(x, y, s, r):
    if r <= 0:
        return False
    m = s - 1 - r
    if x < r and y < r and (x - r) ** 2 + (y - r) ** 2 > r * r:
        return True
    if x >= m and y < r and (x - m) ** 2 + (y - r) ** 2 > r * r:
        return True
    if x < r and y >= m and (x - r) ** 2 + (y - m) ** 2 > r * r:
        return True
    if x >= m and y >= m and (x - m) ** 2 + (y - m) ** 2 > r * r:
        return True
    return False


def _en_triangulo(x, y, a, b, c):
    def cruce(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
    d1 = cruce(a, b, (x, y))
    d2 = cruce(b, c, (x, y))
    d3 = cruce(c, a, (x, y))
    tiene_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    tiene_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (tiene_neg and tiene_pos)


def icon_size(s):
    r = max(2, round(s * 0.09))
    m = round(s * 0.13)
    th = max(2, round(s * 0.085))
    b0 = m
    b1 = s - m - 1
    x1 = b0 + round((b1 - b0) * 0.14)
    x3 = b0 + round((b1 - b0) * 0.86)
    x2 = b0 + round((b1 - b0) * 0.50)
    top = m
    bot = s - m - 1
    tri = ((x1, top), (x3, top), (x2, bot))
    cambia = round(s * 0.62)
    filas = []
    for y in range(s):
        fila = []
        f = y / max(1, s - 1)
        bg = tuple(round(FONDO_A[i] + (FONDO_B[i] - FONDO_A[i]) * f) for i in range(3)) + (255,)
        for x in range(s):
            if _fuera_de_esquina(x, y, s, r):
                fila.append((0, 0, 0, 0))
                continue
            en = False
            if x1 - th / 2 <= x <= x1 + th / 2 and top <= y <= bot:
                en = True
            if x3 - th / 2 <= x <= x3 + th / 2 and top <= y <= bot:
                en = True
            if _en_triangulo(x, y, *tri):
                en = True
            if en:
                fila.append(VERDE if y < cambia else VERDE_B)
            else:
                fila.append(bg)
        filas.append(fila)
    return filas


def _png_chunk(tipo, datos):
    c = tipo + datos
    return struct.pack(">I", len(datos)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)


def png_rgba(filas):
    h = len(filas)
    w = len(filas[0])
    raw = b"".join(b"\x00" + b"".join(bytes(px) for px in fila) for fila in filas)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )


def crear_ico(ruta, tamanos=(256, 48, 32)):
    pngs = [png_rgba(icon_size(s)) for s in tamanos]
    off = 6 + 16 * len(pngs)
    cabecera = struct.pack("<HHH", 0, 1, len(pngs))
    for s, png in zip(tamanos, pngs):
        wb = 0 if s == 256 else s
        cabecera += struct.pack("<BBBBHHII", wb, wb, 0, 0, 1, 32, len(png), off)
        off += len(png)
    blob = cabecera + b"".join(pngs)
    with open(ruta, "wb") as f:
        f.write(blob)
    return len(blob)


if __name__ == "__main__":
    print(f"app.ico ({crear_ico(DESTINO)} bytes) -> {DESTINO}")