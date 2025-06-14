#!/bin/bash

# Build base image with all dependencies
echo "Building base image..."
docker build -f docker/Dockerfile.base -t agent-service-base:latest .

# Build service image based on the base image
echo "Building service image..."
docker build -f docker/Dockerfile.service -t agent-service:latest .

echo "Build completed!"
echo "Base image: agent-service-base:latest"
echo "Service image: agent-service:latest" 