# Instax-BLEAK

Forked from https://github.com/javl/InstaxBLE

## Control an Instax Link Printer From Python

A python library for asynchronous communication with Instax printers.

## Getting Started

### Include from source

This library is not to a package repository, but it can still be used from the sources

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
