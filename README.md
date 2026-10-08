# Instax-BLEAK

A python library for asynchronous communication with Instax printers.

Forked from https://github.com/javl/InstaxBLE

## Getting Started

### Installation

This library is not published to a package repository, but it can still be used from the source.

#### uv

pyproject.toml
```toml
dependencies = [
    "pyinstaxble",
]

[tool.uv.sources]
pyinstaxble = [
    { git = "ssh://git@github.com/JoRobs/pyinstaxble.git", tag = "v0.3.3" }
]
```

#### Clone
```
git clone git@github.com:JoRobs/pyinstaxble.git
```

### Usage

Connecting and printing an image
```
from anyio import run
from pyinstaxble import InstaxBleak

async def main():
    client = InstaxBleak()
    await client.connect()
    await client.print("resources/example-mini.jpeg")

if __name__ == "__main__":
    run(main)
```

#### License
This project is licensed under the [MIT License](LICENSE.md).
