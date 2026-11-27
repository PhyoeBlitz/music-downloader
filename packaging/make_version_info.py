"""Write a PyInstaller --version-file so the exe carries Windows version metadata
(Properties -> Details: product name, version, copyright).

Usage: python packaging/make_version_info.py <version> <exe-name> <description> <output-file>
Example: python packaging/make_version_info.py 1.0.1 MusicDownloaderGUI "Music Downloader" build/gui_version.txt
"""

import re
import sys
from pathlib import Path

PRODUCT_NAME = "Music Downloader"
COMPANY = "PhyoeBlitz"
COPYRIGHT = "Copyright (c) 2026 PhyoeBlitz. MIT License."

TEMPLATE = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={nums},
    prodvers={nums},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', {company!r}),
        StringStruct('FileDescription', {description!r}),
        StringStruct('FileVersion', {version!r}),
        StringStruct('InternalName', {exe!r}),
        StringStruct('LegalCopyright', {copyright!r}),
        StringStruct('OriginalFilename', {exe_file!r}),
        StringStruct('ProductName', {product!r}),
        StringStruct('ProductVersion', {version!r})
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main():
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    version, exe, description, out = sys.argv[1:]
    version = version.lstrip("v")
    if not re.fullmatch(r"\d+(\.\d+){0,3}", version):
        sys.exit(f"Version must look like 1.2.3, got: {version}")
    nums = tuple((list(map(int, version.split("."))) + [0, 0, 0])[:4])
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(TEMPLATE.format(
        nums=nums, version=version, exe=exe, exe_file=exe + ".exe", description=description,
        company=COMPANY, copyright=COPYRIGHT, product=PRODUCT_NAME,
    ), encoding="utf-8")
    print(f"wrote {out} ({exe} {version})")


if __name__ == "__main__":
    main()
