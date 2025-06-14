#!/bin/bash

docker run -it --rm \
  -v $(pwd):/app \
  python-uv \
  uv add playwright