import os

from version import APP_VERSION


def _partes(version):
    partes = [p for p in version.strip().split(".") if p]
    nums = []
    for p in partes:
        digitos = ""
        for ch in p:
            if ch.isdigit():
                digitos += ch
            else:
                break
        nums.append(int(digitos) if digitos else 0)
    while len(nums) < 3:
        nums.append(0)
    a, b, c = nums[0], nums[1], nums[2]
    return a, b, c, APP_VERSION


def _plantilla():
    a, b, c, version = _partes(APP_VERSION)
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({a}, {b}, {c}, 0),
    prodvers=({a}, {b}, {c}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '040904B0',
          [
            StringStruct('CompanyName', 'Instalador de Mods'),
            StringStruct('FileDescription', 'Instalador y sincronizador de mods de Minecraft'),
            StringStruct('FileVersion', '{version}.0'),
            StringStruct('InternalName', 'InstaladorMods'),
            StringStruct('LegalCopyright', ''),
            StringStruct('OriginalFilename', 'InstaladorMods.exe'),
            StringStruct('ProductName', 'Instalador de Mods de Minecraft'),
            StringStruct('ProductVersion', '{version}.0')
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main():
    directorio = os.path.dirname(os.path.abspath(__file__))
    ruta = os.path.join(directorio, "version_info.txt")
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(_plantilla())
    print(f"version_info.txt generado desde version.py ({APP_VERSION})")


if __name__ == "__main__":
    main()