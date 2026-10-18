from pathlib import Path

url = r"D:\python\output\hak180产品安全手册\hak180产品安全手册.md"
path = Path(url)
print(path.stem)
print(path.suffix)